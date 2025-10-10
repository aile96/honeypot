#include "file_mirror_http.h"
#include "flagd_client.h"

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
static std::string g_exposed_path;       // current path
static std::shared_mutex g_exposed_mx;   // RW-lock for concurrent access

static std::string read_env(const char* k, const std::string& def="") {
  if (auto* v = std::getenv(k)) return std::string(v);
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
    if (path.rfind(allow_prefix, 0) != 0) return false; // does not start with prefix
  }
  return true;
}

std::string GetExposedPath() {
  std::shared_lock<std::shared_mutex> lk(g_exposed_mx);
  return g_exposed_path; // copy
}

// ======== flagd watcher ========
void StartFlagWatcher() {
  std::thread([]{
    const auto host = read_env("FLAGD_HOST","flagd.mem");
    const int  port = std::atoi(read_env("FLAGD_PORT","8013").c_str());
    const auto key  = read_env("EXPOSED_FLAG_KEY","exposed_path");
    const int  poll = std::atoi(read_env("POLL_TIME","10").c_str());

    for (;;) {
      // 1) resolve the path from flagd (as already done)
      if (auto val = FlagdResolveString(host, port, key)) {
        if (allowed_path(*val)) {
          std::unique_lock<std::shared_mutex> lk(g_exposed_mx);
          g_exposed_path = *val;
        }
      }

      // 2) update the rates from the DB
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

  // catch-all: responds only if the requested path matches the flag's value
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
