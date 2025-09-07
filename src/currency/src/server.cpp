// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

#include <cstdlib>
#include <iostream>
#include <math.h>
#include <demo.grpc.pb.h>
#include <grpc/health/v1/health.grpc.pb.h>

#include "opentelemetry/trace/context.h"
#include "opentelemetry/trace/semantic_conventions.h"
#include "opentelemetry/trace/span_context_kv_iterable_view.h"
#include "opentelemetry/baggage/baggage.h"
#include "opentelemetry/nostd/string_view.h"
#include "logger_common.h"
#include "meter_common.h"
#include "tracer_common.h"

#include <grpcpp/grpcpp.h>
#include <grpcpp/server.h>
#include <grpcpp/server_builder.h>
#include <grpcpp/server_context.h>
#include <grpcpp/impl/codegen/string_ref.h>

#include "opentelemetry/common/key_value_iterable_view.h"
#include <string>
#include <unordered_map>
#include <map>
#include <libpq-fe.h>

// Connessione globale inizializzata all'avvio
PGconn *db_conn;

void init_db_connection() {
    const std::string db_host = getenv("DB_HOST");
    const std::string db_name = getenv("DB_NAME");
    const std::string db_user = getenv("DB_USER");
    const std::string db_pass = getenv("DB_PASS");
    const std::string db_port = getenv("DB_PORT");

    std::string conn_str = "host=" + db_host +
                           " port=" + db_port +
                           " dbname=" + db_name +
                           " user=" + db_user +
                           " password=" + db_pass +
                           " connect_timeout=5";
    std::cerr << "[currency] Connecting to "
              << db_host << ":" << db_port
              << " db=" << db_name << " user=" << db_user << std::endl;

    db_conn = PQconnectdb(conn_str.c_str());

    if (PQstatus(db_conn) != CONNECTION_OK) {
        std::cerr << "Connection to DB failed: " << PQerrorMessage(db_conn) << std::endl;
        PQfinish(db_conn);
        std::exit(1);
    } else {
      std::cerr << "Connection to DB OK" << std::endl;
    }
}


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

namespace
{
  std::unordered_map<std::string, double> currency_conversion;

  void update_currency_conversion() {
     if (!db_conn || PQstatus(db_conn) != CONNECTION_OK) {
          std::cerr << "DB connection not ready; attempting reconnect..." << std::endl;
          init_db_connection();
      }
      PGresult* res = PQexec(db_conn, "SELECT code, rate FROM currency_rates");
  
      if (PQresultStatus(res) != PGRES_TUPLES_OK) {
          std::cerr << "Query failed: " << PQerrorMessage(db_conn) << std::endl;
          PQclear(res);
          return;
      }
    
      currency_conversion.clear();
      int rows = PQntuples(res);
      for (int i = 0; i < rows; i++) {
          std::string code = PQgetvalue(res, i, 0);
          double rate = std::stod(PQgetvalue(res, i, 1));
          currency_conversion[code] = rate;
      }
    
      PQclear(res);
  }

  std::string version = getenv("VERSION");
  std::string name{ "currency" };

  nostd::unique_ptr<metrics_api::Counter<uint64_t>> currency_counter;
  nostd::shared_ptr<opentelemetry::logs::Logger> logger;

class HealthServer final : public grpc::health::v1::Health::Service
{
  Status Check(
    ServerContext* context,
    const grpc::health::v1::HealthCheckRequest* request,
    grpc::health::v1::HealthCheckResponse* response) override
  {
    response->set_status(grpc::health::v1::HealthCheckResponse::SERVING);
    return Status::OK;
  }
};

class CurrencyService final : public oteldemo::CurrencyService::Service
{
  Status GetSupportedCurrencies(ServerContext* context,
  	const Empty* request,
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

    update_currency_conversion();
    for (auto &code : currency_conversion) {
      response->add_currency_codes(code.first);
    }

    span->AddEvent("Currencies fetched, response sent back");
    span->SetStatus(StatusCode::kOk);

    logger->Info(std::string(__func__) + " successful");

    // Make sure to end your spans!
    span->End();
  	return Status::OK;
  }

  double getDouble(Money& money) {
    auto units = money.units();
    auto nanos = money.nanos();

    double decimal = 0.0;
    while (nanos != 0) {
      double t = (double)(nanos%10)/10;
      nanos = nanos/10;
      decimal = decimal/10 + t;
    }

    return double(units) + decimal;
  }

  void getUnitsAndNanos(Money& money, double value) {
    long unit = (long)value;
    double rem = value - unit;
    long nano = rem * pow(10, 9);
    money.set_units(unit);
    money.set_nanos(nano);
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
      // Do the conversion work
      Money from = request->from();
      string from_code = from.currency_code();
      double rate = currency_conversion[from_code];
      double one_euro = getDouble(from) / rate ;

      string to_code = request->to_code();
      double to_rate = currency_conversion[to_code];

      double final = one_euro * to_rate;
      getUnitsAndNanos(*response, final);
      response->set_currency_code(to_code);

      span->SetAttribute("app.currency.conversion.from", from_code);
      span->SetAttribute("app.currency.conversion.to", to_code);

      CurrencyCounter(to_code);

      span->AddEvent("Conversion successful, response sent back");
      span->SetStatus(StatusCode::kOk);

      logger->Info(std::string(__func__) + " conversion successful");
      
      // End the span
      span->End();
      return Status::OK;

    } catch(...) {
      span->AddEvent("Conversion failed");
      span->SetStatus(StatusCode::kError);

      logger->Error(std::string(__func__) + " conversion failure");

      span->End();
      return Status::CANCELLED;
    }
    return Status::OK;
  }

  void CurrencyCounter(const std::string& currency_code)
  {
      std::map<std::string, std::string> labels = { {"currency_code", currency_code} };
      auto labelkv = common::KeyValueIterableView<decltype(labels)>{ labels };
      currency_counter->Add(1, labelkv);
  }
};

void RunServer(uint16_t port)
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
}

int main(int argc, char **argv) {

  if (argc < 2) {
    std::cout << "Usage: currency <port>";
    return 0;
  }

  uint16_t port = atoi(argv[1]);

  initTracer();
  initMeter();
  initLogger();
  init_db_connection();
  currency_counter = initIntCounter("app.currency", version);
  logger = getLogger(name);
  RunServer(port);

  // clean shutdown
  if (db_conn) {
    PQfinish(db_conn);
    db_conn = nullptr;
  }

  return 0;
}
