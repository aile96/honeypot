#include "file_mirror_http.h"
#include "flagd_client.h"
#include "logger_common.h"
#include "meter_common.h"
#include "tracer_common.h"

#include "opentelemetry/common/key_value_iterable_view.h"
#include "opentelemetry/trace/provider.h"

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cstdlib>
#include <fstream>
#include <map>
#include <mutex>
#include <shared_mutex>
#include <sstream>
#include <string>
#include <thread>

#include "../third_party/httplib.h"
#include "server.hpp"

namespace trace_api = opentelemetry::trace;

// ========= global state =========
static std::string g_exposed_path;       // current path being served
static std::shared_mutex g_exposed_mx;   // RW-lock for concurrent access
static nostd::unique_ptr<metrics_api::Counter<uint64_t>> g_http_request_counter;
static nostd::unique_ptr<metrics_api::Counter<uint64_t>> g_flag_update_counter;

// ========= helpers =========
static std::string read_env(const char* k, const std::string& def = "") {
  if (const char* v = std::getenv(k)) return std::string(v);
  return def;
}

static bool read_env_bool(const char* k, bool def = false) {
  if (const char* v = std::getenv(k)) {
    std::string s(v);
    std::transform(s.begin(), s.end(), s.begin(),
                   [](unsigned char c) { return std::tolower(c); });
    return (s == "1" || s == "true" || s == "yes" || s == "on");
  }
  return def;
}

static std::string read_file(const std::string& path, bool& ok) {
  std::ifstream ifs(path, std::ios::binary);
  if (!ifs) { ok = false; return {}; }
  std::ostringstream oss; oss << ifs.rdbuf();
  ok = true; return oss.str();
}

static bool allowed_path(const std::string& path) {
  const bool require_abs = read_env("EXPOSE_REQUIRE_ABSOLUTE","1") == "1";
  if (require_abs && (path.empty() || path[0] != '/')) return false;

  const auto allow_prefix = read_env("EXPOSE_ALLOW_PREFIX","/");
  if (!allow_prefix.empty()) {
    if (path.rfind(allow_prefix, 0) != 0) return false; // doesn't start with prefix
  }
  return true;
}

static void init_file_mirror_telemetry() {
  if (!g_http_request_counter) {
    g_http_request_counter = initIntCounter("app.currency.file_mirror_http.requests", "v1");
  }
  if (!g_flag_update_counter) {
    g_flag_update_counter = initIntCounter("app.currency.flag_watcher.updates", "v1");
  }
}

static void increment_counter(
    const nostd::unique_ptr<metrics_api::Counter<uint64_t>>& counter,
    const std::map<std::string, std::string>& labels) {
  if (!counter) {
    return;
  }
  auto labelkv = common::KeyValueIterableView<std::map<std::string, std::string>>{labels};
  counter->Add(1, labelkv);
}

std::string GetExposedPath() {
  std::shared_lock<std::shared_mutex> lk(g_exposed_mx);
  return g_exposed_path; // copy
}

// ======== flagd/static watcher ========
void StartFlagWatcher() {
  init_file_mirror_telemetry();
  std::thread([]{
    auto logger = getLogger("currency");
    auto tracer = get_tracer("currency");
    const auto host     = read_env("FLAGD_HOST","flagd.mem");
    const int  port     = std::atoi(read_env("FLAGD_PORT","8013").c_str());
    const auto key      = read_env("EXPOSED_FLAG_KEY","exposed_path");
    const int  poll     = std::atoi(read_env("POLL_TIME","10").c_str());
    const auto fallback = read_env("EXPOSE_STATIC_PATH","/tmp/log.txt");

    // Initialization: if in static mode, set fallback immediately
    {
      std::unique_lock<std::shared_mutex> lk(g_exposed_mx);
      if (!allowed_path(g_exposed_path)) g_exposed_path.clear();
      if (!read_env_bool("EXPOSE_USE_FLAGD", /*def=*/true)) {
        if (allowed_path(fallback)) {
          g_exposed_path = fallback;
          logger->Info("[flag] using static path (startup): " + g_exposed_path);
          increment_counter(g_flag_update_counter, {{"source", "static"}, {"result", "startup_set"}});
        } else {
          logger->Warn("[flag] EXPOSE_STATIC_PATH not allowed; leaving empty");
          increment_counter(g_flag_update_counter, {{"source", "static"}, {"result", "startup_rejected"}});
        }
      }
    }

    for (;;) {
      const bool use_flagd = read_env_bool("EXPOSE_USE_FLAGD", /*def=*/true);
      auto span = tracer->StartSpan("Currency/FlagWatcher/Poll");
      auto scope = tracer->WithActiveSpan(span);
      span->SetAttribute("app.flagd.use_flagd", use_flagd);
      span->SetAttribute("app.flagd.host", host);
      span->SetAttribute("app.flagd.port", port);
      span->SetAttribute("app.flagd.key", key);
      increment_counter(
          g_flag_update_counter,
          {{"source", use_flagd ? "flagd" : "static"}, {"result", "poll"}});

      if (use_flagd) {
        // flagd mode: resolve path and update if valid
        if (auto val = FlagdResolveString(host, port, key)) {
          span->SetAttribute("app.flagd.resolved_path", *val);
          if (allowed_path(*val)) {
            std::unique_lock<std::shared_mutex> lk(g_exposed_mx);
            if (g_exposed_path != *val) {
              g_exposed_path = *val;
              logger->Info("[flag] updated from flagd: " + g_exposed_path);
              increment_counter(g_flag_update_counter, {{"source", "flagd"}, {"result", "updated"}});
            }
          } else {
            logger->Warn("[flag] value from flagd rejected by allowed_path");
            increment_counter(g_flag_update_counter, {{"source", "flagd"}, {"result", "rejected"}});
          }
        } else {
          span->AddEvent("Flagd value unavailable; keeping previous path");
          increment_counter(g_flag_update_counter, {{"source", "flagd"}, {"result", "unavailable"}});
        }
      } else {
        // static mode: always set/update the fallback value
        if (allowed_path(fallback)) {
          std::unique_lock<std::shared_mutex> lk(g_exposed_mx);
          if (g_exposed_path != fallback) {
            g_exposed_path = fallback;
            logger->Info("[flag] using static path: " + g_exposed_path);
            increment_counter(g_flag_update_counter, {{"source", "static"}, {"result", "updated"}});
          }
        } else {
          logger->Warn("[flag] EXPOSE_STATIC_PATH not allowed; path unchanged");
          increment_counter(g_flag_update_counter, {{"source", "static"}, {"result", "rejected"}});
        }
      }

      // Periodically update currency rates (independent of flag mode)
      try {
        update_currency_conversion();
        span->AddEvent("Rates refresh completed");
        span->SetStatus(trace_api::StatusCode::kOk);
      } catch (const std::exception& e) {
        logger->Error(std::string("[rates] update failed: ") + e.what());
        span->SetStatus(trace_api::StatusCode::kError);
      }

      span->End();
      std::this_thread::sleep_for(std::chrono::seconds(poll));
    }
  }).detach();
}

// ======== HTTP server ========
void StartFileMirrorHttp() {
  init_file_mirror_telemetry();
  auto logger = getLogger("currency");
  auto tracer = get_tracer("currency");
  int port = std::atoi(read_env("EXPOSE_HTTP_PORT","8081").c_str());
  auto* svr = new httplib::Server();

  svr->Get("/healthz", [tracer](const httplib::Request&, httplib::Response& res){
    auto span = tracer->StartSpan("Currency/FileMirror/Healthz");
    auto scope = tracer->WithActiveSpan(span);
    span->SetAttribute("http.method", "GET");
    span->SetAttribute("http.route", "/healthz");
    res.set_content("ok", "text/plain");
    res.status = 200;
    span->SetAttribute("http.status_code", res.status);
    span->SetStatus(trace_api::StatusCode::kOk);
    increment_counter(g_http_request_counter, {{"route", "/healthz"}, {"status", "200"}});
    span->End();
  });

  // Catch-all: only respond if requested path matches the exposed flag value
  svr->Get(R"((/.*))", [tracer](const httplib::Request& req, httplib::Response& res) {
    auto span = tracer->StartSpan("Currency/FileMirror/ServePath");
    auto scope = tracer->WithActiveSpan(span);
    span->SetAttribute("http.method", "GET");
    span->SetAttribute("http.route", "/*");
    span->SetAttribute("http.target", req.path);
    std::string exposed;
    {
      std::shared_lock<std::shared_mutex> lk(g_exposed_mx);
      exposed = g_exposed_path;
    }
    span->SetAttribute("app.file_mirror.exposed_path", exposed);
    if (exposed.empty() || !allowed_path(exposed)) {
      res.status = 404;
      res.set_content("not configured", "text/plain");
      span->AddEvent("Exposed path not configured");
      span->SetAttribute("http.status_code", res.status);
      span->SetStatus(trace_api::StatusCode::kError);
      increment_counter(g_http_request_counter, {{"route", "/*"}, {"status", "404"}, {"reason", "not_configured"}});
      span->End();
      return;
    }
    if (req.path != exposed) {
      res.status = 404;
      res.set_content("not found", "text/plain");
      span->SetAttribute("http.status_code", res.status);
      span->SetStatus(trace_api::StatusCode::kOk);
      increment_counter(g_http_request_counter, {{"route", "/*"}, {"status", "404"}, {"reason", "not_found"}});
      span->End();
      return;
    }
    bool ok = false;
    auto body = read_file(exposed, ok);
    if (!ok) {
      res.status = 404;
      res.set_content("file not found", "text/plain");
      span->AddEvent("Configured file not found");
      span->SetAttribute("http.status_code", res.status);
      span->SetStatus(trace_api::StatusCode::kError);
      increment_counter(g_http_request_counter, {{"route", "/*"}, {"status", "404"}, {"reason", "file_missing"}});
      span->End();
      return;
    }
    res.status = 200;
    res.set_content(body, "application/octet-stream");
    span->SetAttribute("http.status_code", res.status);
    span->SetAttribute("app.file_mirror.response_size", static_cast<int64_t>(body.size()));
    span->SetStatus(trace_api::StatusCode::kOk);
    increment_counter(g_http_request_counter, {{"route", "/*"}, {"status", "200"}, {"reason", "served"}});
    span->End();
  });

  std::thread([svr, port, logger]{
    logger->Info("[http] listening on :" + std::to_string(port));
    svr->listen("0.0.0.0", port);
    logger->Warn("[http] server stopped listening on :" + std::to_string(port));
  }).detach();
}
