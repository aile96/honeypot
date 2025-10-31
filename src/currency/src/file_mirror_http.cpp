#include "file_mirror_http.h"
#include "flagd_client.h"

#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <mutex>
#include <shared_mutex>
#include <sstream>
#include <string>
#include <thread>

#include "../third_party/httplib.h"
#include "server.hpp"

// ========= global state =========
static std::string g_exposed_path;       // current path being served
static std::shared_mutex g_exposed_mx;   // RW-lock for concurrent access

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

std::string GetExposedPath() {
  std::shared_lock<std::shared_mutex> lk(g_exposed_mx);
  return g_exposed_path; // copy
}

// ======== flagd/static watcher ========
void StartFlagWatcher() {
  std::thread([]{
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
          std::cout << "[flag] using static path (startup): " << g_exposed_path << "\n";
        } else {
          std::cerr << "[flag] EXPOSE_STATIC_PATH not allowed; leaving empty\n";
        }
      }
    }

    for (;;) {
      const bool use_flagd = read_env_bool("EXPOSE_USE_FLAGD", /*def=*/true);

      if (use_flagd) {
        // flagd mode: resolve path and update if valid
        if (auto val = FlagdResolveString(host, port, key)) {
          if (allowed_path(*val)) {
            std::unique_lock<std::shared_mutex> lk(g_exposed_mx);
            if (g_exposed_path != *val) {
              g_exposed_path = *val;
              std::cout << "[flag] updated from flagd: " << g_exposed_path << "\n";
            }
          } else {
            std::cerr << "[flag] value from flagd rejected by allowed_path\n";
          }
        } // if flagd fails, keep the last valid value
      } else {
        // static mode: always set/update the fallback value
        if (allowed_path(fallback)) {
          std::unique_lock<std::shared_mutex> lk(g_exposed_mx);
          if (g_exposed_path != fallback) {
            g_exposed_path = fallback;
            std::cout << "[flag] using static path: " << g_exposed_path << "\n";
          }
        } else {
          std::cerr << "[flag] EXPOSE_STATIC_PATH not allowed; path unchanged\n";
        }
      }

      // Periodically update currency rates (independent of flag mode)
      try {
        update_currency_conversion();
      } catch (const std::exception& e) {
        std::cerr << "[rates] update failed: " << e.what() << "\n";
      }

      std::this_thread::sleep_for(std::chrono::seconds(poll));
    }
  }).detach();
}

// ======== HTTP server ========
void StartFileMirrorHttp() {
  int port = std::atoi(read_env("EXPOSE_HTTP_PORT","8081").c_str());
  auto* svr = new httplib::Server();

  svr->Get("/healthz", [](const httplib::Request&, httplib::Response& res){
    res.set_content("ok", "text/plain");
  });

  // Catch-all: only respond if requested path matches the exposed flag value
  svr->Get(R"((/.*))", [](const httplib::Request& req, httplib::Response& res) {
    std::string exposed;
    {
      std::shared_lock<std::shared_mutex> lk(g_exposed_mx);
      exposed = g_exposed_path;
    }
    if (exposed.empty() || !allowed_path(exposed)) {
      res.status = 404; res.set_content("not configured", "text/plain"); return;
    }
    if (req.path != exposed) {
      res.status = 404; res.set_content("not found", "text/plain"); return;
    }
    bool ok = false;
    auto body = read_file(exposed, ok);
    if (!ok) {
      res.status = 404; res.set_content("file not found", "text/plain"); return;
    }
    res.status = 200;
    res.set_content(body, "application/octet-stream");
  });

  std::thread([svr, port]{
    std::cout << "[http] listening on :" << port << "\n";
    svr->listen("0.0.0.0", port);
  }).detach();
}
