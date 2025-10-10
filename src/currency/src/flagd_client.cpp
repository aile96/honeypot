#include "flagd_client.h"
#include <cstdlib>
#include <string>
#include <optional>
#include <sstream>
#include <iostream>

#include "../third_party/httplib.h"
#include "../third_party/json.hpp"

using nlohmann::json;

// flagd HTTP Evaluation API (ResolveString)
// POST http://{host}:{port}/flagd.evaluation.v1.Service/ResolveString
// Body: {"flagKey":"...", "context":{}}
std::optional<std::string> FlagdResolveString(
    const std::string& host,
    int port,
    const std::string& flag_key)
{
  try {
    httplib::Client cli(host.c_str(), port);
    cli.set_read_timeout(2, 0);
    cli.set_write_timeout(2, 0);

    json body;
    body["flagKey"] = flag_key;
    body["context"] = json::object();

    auto res = cli.Post("/flagd.evaluation.v1.Service/ResolveString",
                        body.dump(), "application/json");
    if (!res) {
      std::cerr << "[flagd] no response\n";
      return std::nullopt;
    }
    if (res->status != 200) {
      std::cerr << "[flagd] status: " << res->status << "\n";
      return std::nullopt;
    }
    auto j = json::parse(res->body, nullptr, false);
    if (j.is_discarded()) {
      std::cerr << "[flagd] invalid JSON\n";
      return std::nullopt;
    }
    // Typical response: {"value": "STRING", ...}
    if (j.contains("value") && j["value"].is_string()) {
      return j["value"].get<std::string>();
    }
    std::cerr << "[flagd] missing 'value' field or not a string\n";
    return std::nullopt;
  } catch (const std::exception& e) {
    std::cerr << "[flagd] exception: " << e.what() << "\n";
    return std::nullopt;
  }
}
