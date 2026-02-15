#include "flagd_client.h"
#include "logger_common.h"
#include <cstdlib>
#include <string>
#include <optional>
#include <sstream>

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
      getLogger("currency")->Warn("[flagd] no response");
      return std::nullopt;
    }
    if (res->status != 200) {
      getLogger("currency")->Warn("[flagd] status: " + std::to_string(res->status));
      return std::nullopt;
    }
    auto j = json::parse(res->body, nullptr, false);
    if (j.is_discarded()) {
      getLogger("currency")->Warn("[flagd] invalid JSON");
      return std::nullopt;
    }
    // Typical response: {"value": "STRING", ...}
    if (j.contains("value") && j["value"].is_string()) {
      return j["value"].get<std::string>();
    }
    getLogger("currency")->Warn("[flagd] missing 'value' field or not a string");
    return std::nullopt;
  } catch (const std::exception& e) {
    getLogger("currency")->Error(std::string("[flagd] exception: ") + e.what());
    return std::nullopt;
  }
}
