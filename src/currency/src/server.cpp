// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

#include <cstdlib>
#include <iostream>
#include <cmath>
#include <string>
#include <unordered_map>
#include <map>
#include <shared_mutex>

#include <libpq-fe.h>

#include <grpcpp/grpcpp.h>
#include <grpcpp/server.h>
#include <grpcpp/server_builder.h>
#include <grpcpp/server_context.h>
#include <grpcpp/impl/codegen/string_ref.h>

#include <demo.grpc.pb.h>
#include <grpc/health/v1/health.grpc.pb.h>

#include "opentelemetry/trace/context.h"
#include "opentelemetry/trace/semantic_conventions.h"
#include "opentelemetry/trace/span_context_kv_iterable_view.h"
#include "opentelemetry/baggage/baggage.h"
#include "opentelemetry/nostd/string_view.h"
#include "opentelemetry/common/key_value_iterable_view.h"

#include "logger_common.h"
#include "meter_common.h"
#include "tracer_common.h"

#include "file_mirror_http.h"  // StartFlagWatcher / StartFileMirrorHttp

// ========================
// Helpers/env
// ========================
static std::string get_env_str(const char *k, const std::string &def = "") {
  if (const char *v = std::getenv(k)) return std::string(v);
  return def;
}

// ========================
// Global DB
// ========================
PGconn *db_conn = nullptr;

void init_db_connection() {
  const std::string db_host = get_env_str("DB_HOST", "currency-db");
  const std::string db_name = get_env_str("DB_NAME", "currency");
  const std::string db_user = get_env_str("DB_USER", "postgres");
  const std::string db_pass = get_env_str("DB_PASS", "postgres");
  const std::string db_port = get_env_str("DB_PORT", "5432");

  std::string conn_str = "host=" + db_host +
                         " port=" + db_port +
                         " dbname=" + db_name +
                         " user=" + db_user +
                         " password=" + db_pass +
                         " connect_timeout=5";

  std::cerr << "[currency] Connecting to " << db_host << ":" << db_port
            << " db=" << db_name << " user=" << db_user << std::endl;

  if (db_conn) {
    PQfinish(db_conn);
    db_conn = nullptr;
  }

  db_conn = PQconnectdb(conn_str.c_str());

  if (PQstatus(db_conn) != CONNECTION_OK) {
    std::cerr << "[currency] Connection to DB failed: " << PQerrorMessage(db_conn) << std::endl;
    PQfinish(db_conn);
    db_conn = nullptr;
    std::exit(1);
  } else {
    std::cerr << "[currency] Connection to DB OK" << std::endl;
  }
}

// ========================
// OpenTelemetry / gRPC
// ========================
using namespace std;

using oteldemo::Empty;
using oteldemo::GetSupportedCurrenciesResponse;
using oteldemo::CurrencyConversionRequest;
using oteldemo::Money;

using grpc::Status;
using grpc::ServerContext;
using grpc::ServerBuilder;
using grpc::Server;

using Span        = opentelemetry::trace::Span;
using SpanContext = opentelemetry::trace::SpanContext;
using namespace opentelemetry::trace;
using namespace opentelemetry::baggage;
namespace context = opentelemetry::context;
namespace common  = opentelemetry::common;
namespace metrics_api = opentelemetry::metrics;
namespace nostd       = opentelemetry::nostd;

// ========================
// Tassi in memoria + lock
// ========================
std::unordered_map<std::string, double> currency_conversion;
std::shared_mutex currency_mx;

// Aggiorna la mappa dei tassi dal DB (safe per concorrenza)
void update_currency_conversion() {
  if (!db_conn || PQstatus(db_conn) != CONNECTION_OK) {
    std::cerr << "[rates] DB connection not ready; attempting reconnect..." << std::endl;
    init_db_connection();
  }

  PGresult* res = PQexec(db_conn, "SELECT code, rate FROM currency");
  if (PQresultStatus(res) != PGRES_TUPLES_OK) {
    std::cerr << "[rates] Query failed: " << PQerrorMessage(db_conn) << std::endl;
    PQclear(res);
    return;
  }

  std::unordered_map<std::string,double> tmp;
  const int rows = PQntuples(res);
  for (int i = 0; i < rows; i++) {
    std::string code = PQgetvalue(res, i, 0);
    double rate = std::stod(PQgetvalue(res, i, 1));
    tmp[code] = rate;
  }
  PQclear(res);

  {
    std::unique_lock<std::shared_mutex> lk(currency_mx);
    currency_conversion.swap(tmp);
  }

  std::cerr << "[rates] updated. entries=" << rows << std::endl;
}

// ========================
// Versione/metriche/log
// ========================
std::string version = get_env_str("VERSION", "");
std::string name{ "currency" };

nostd::unique_ptr<metrics_api::Counter<uint64_t>> currency_counter;
nostd::shared_ptr<opentelemetry::logs::Logger> logger;

// ========================
// Health service
// ========================
class HealthServer final : public grpc::health::v1::Health::Service
{
  Status Check(
    ServerContext* /*context*/,
    const grpc::health::v1::HealthCheckRequest* /*request*/,
    grpc::health::v1::HealthCheckResponse* response) override
  {
    response->set_status(grpc::health::v1::HealthCheckResponse::SERVING);
    return Status::OK;
  }
};

// ========================
// Utility Money
// ========================
static double getDouble(Money& money) {
  auto units = money.units();
  auto nanos = money.nanos();

  double decimal = 0.0;
  while (nanos != 0) {
    double t = (double)(nanos % 10) / 10.0;
    nanos = nanos / 10;
    decimal = decimal / 10.0 + t;
  }
  return static_cast<double>(units) + decimal;
}

static void getUnitsAndNanos(Money& money, double value) {
  long unit = static_cast<long>(value);
  double rem = value - static_cast<double>(unit);
  long nano = static_cast<long>(rem * std::pow(10.0, 9.0));
  money.set_units(unit);
  money.set_nanos(nano);
}

// ========================
// Currency Service
// ========================
class CurrencyService final : public oteldemo::CurrencyService::Service
{
  void CurrencyCounter(const std::string& currency_code)
  {
      std::map<std::string, std::string> labels = { {"currency_code", currency_code} };
      auto labelkv = common::KeyValueIterableView<decltype(labels)>{ labels };
      currency_counter->Add(1, labelkv);
  }

public:
  Status GetSupportedCurrencies(ServerContext* context,
  	const Empty* /*request*/,
  	GetSupportedCurrenciesResponse* response) override
  {
    StartSpanOptions options;
    options.kind = SpanKind::kServer;
    GrpcServerCarrier carrier(context);

    auto prop        = context::propagation::GlobalTextMapPropagator::GetGlobalPropagator();
    auto current_ctx = context::RuntimeContext::GetCurrent();
    auto new_context = prop->Extract(carrier, current_ctx);
    options.parent   = GetSpan(new_context)->GetContext();

    std::string span_name = "Currency/GetSupportedCurrencies";
    auto span =
        get_tracer("currency")->StartSpan(span_name,
                                      {{SemanticConventions::kRpcSystem, "grpc"},
                                       {SemanticConventions::kRpcService, "oteldemo.CurrencyService"},
                                       {SemanticConventions::kRpcMethod, "GetSupportedCurrencies"},
                                       {SemanticConventions::kRpcGrpcStatusCode, 0}},
                                      options);
    auto scope = get_tracer("currency")->WithActiveSpan(span);

    span->AddEvent("Processing supported currencies request");

    // (non necessario se il watcher aggiorna già in background, ma non fa male se vuoi forzare un refresh)
    // update_currency_conversion();

    {
      std::shared_lock<std::shared_mutex> lk(currency_mx);
      for (const auto &kv : currency_conversion) {
        response->add_currency_codes(kv.first);
      }
    }

    span->AddEvent("Currencies fetched, response sent back");
    span->SetStatus(StatusCode::kOk);

    logger->Info(std::string(__func__) + " successful");
    span->End();
  	return Status::OK;
  }

  Status Convert(ServerContext* context,
  	const CurrencyConversionRequest* request,
  	Money* response) override
  {
    StartSpanOptions options;
    options.kind = SpanKind::kServer;
    GrpcServerCarrier carrier(context);

    auto prop        = context::propagation::GlobalTextMapPropagator::GetGlobalPropagator();
    auto current_ctx = context::RuntimeContext::GetCurrent();
    auto new_context = prop->Extract(carrier, current_ctx);
    options.parent   = GetSpan(new_context)->GetContext();

    std::string span_name = "Currency/Convert";
    auto span =
        get_tracer("currency")->StartSpan(span_name,
                                      {{SemanticConventions::kRpcSystem, "grpc"},
                                       {SemanticConventions::kRpcService, "oteldemo.CurrencyService"},
                                       {SemanticConventions::kRpcMethod, "Convert"},
                                       {SemanticConventions::kRpcGrpcStatusCode, 0}},
                                      options);
    auto scope = get_tracer("currency")->WithActiveSpan(span);

    span->AddEvent("Processing currency conversion request");

    try {
      // Lettura tassi thread-safe
      Money from = request->from();
      const std::string from_code = from.currency_code();
      const std::string to_code   = request->to_code();

      double from_rate, to_rate;
      {
        std::shared_lock<std::shared_mutex> lk(currency_mx);
        auto it_from = currency_conversion.find(from_code);
        auto it_to   = currency_conversion.find(to_code);
        if (it_from == currency_conversion.end() || it_to == currency_conversion.end()) {
          throw std::runtime_error("valuta non supportata");
        }
        from_rate = it_from->second;
        to_rate   = it_to->second;
      }

      // Conversione: da importo "from" alla base (EUR) e poi a "to"
      const double one_euro = getDouble(from) / from_rate;
      const double final = one_euro * to_rate;
      getUnitsAndNanos(*response, final);
      response->set_currency_code(to_code);

      span->SetAttribute("app.currency.conversion.from", from_code);
      span->SetAttribute("app.currency.conversion.to", to_code);

      CurrencyCounter(to_code);

      span->AddEvent("Conversion successful, response sent back");
      span->SetStatus(StatusCode::kOk);

      logger->Info(std::string(__func__) + " conversion successful");
      span->End();
      return Status::OK;

    } catch(const std::exception &e) {
      span->AddEvent(std::string("Conversion failed: ") + e.what());
      span->SetStatus(StatusCode::kError);
      logger->Error(std::string(__func__) + " conversion failure: " + e.what());
      span->End();
      return Status::CANCELLED;
    } catch(...) {
      span->AddEvent("Conversion failed (unknown)");
      span->SetStatus(StatusCode::kError);
      logger->Error(std::string(__func__) + " conversion failure (unknown)");
      span->End();
      return Status::CANCELLED;
    }
  }
};

// ========================
// Avvio gRPC
// ========================
static void RunServer(uint16_t port)
{
  std::string address("0.0.0.0:" + std::to_string(port));
  CurrencyService currencyService;
  HealthServer healthService;
  ServerBuilder builder;

  builder.RegisterService(&currencyService);
  builder.RegisterService(&healthService);
  builder.AddListeningPort(address, grpc::InsecureServerCredentials());

  std::unique_ptr<Server> server(builder.BuildAndStart());
  logger->Info("Currency Server listening on port: " + address);
  server->Wait();
  server->Shutdown();
}

// ========================
// main
// ========================
int main(int argc, char **argv) {
  if (argc < 2) {
    std::cout << "Usage: currency <port>";
    return 0;
  }
  uint16_t port = static_cast<uint16_t>(std::atoi(argv[1]));

  // Avvio HTTP mirror e watcher flagd (legge path e — se integrato — può anche invocare update_currency_conversion)
  StartFlagWatcher();
  StartFileMirrorHttp();

  // OpenTelemetry
  initTracer();
  initMeter();
  initLogger();
  currency_counter = initIntCounter("app.currency", version);
  logger = getLogger(name);

  // DB + primi tassi
  init_db_connection();
  update_currency_conversion();  // primo caricamento

  RunServer(port);

  // clean shutdown
  if (db_conn) {
    PQfinish(db_conn);
    db_conn = nullptr;
  }
  return 0;
}
