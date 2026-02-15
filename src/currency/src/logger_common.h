// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

#include "opentelemetry/exporters/otlp/otlp_grpc_exporter_factory.h"
#include "opentelemetry/logs/provider.h"
#include "opentelemetry/sdk/logs/logger.h"
#include "opentelemetry/sdk/logs/logger_provider.h"
#include "opentelemetry/sdk/logs/logger_provider_factory.h"
#include "opentelemetry/sdk/logs/simple_log_record_processor_factory.h"
#include "opentelemetry/sdk/logs/logger_context_factory.h"
#include "opentelemetry/exporters/otlp/otlp_grpc_log_record_exporter_factory.h"

#include <memory>
#include <vector>

using namespace std;
namespace nostd     = opentelemetry::nostd;
namespace otlp      = opentelemetry::exporter::otlp;
namespace logs      = opentelemetry::logs;
namespace logs_sdk  = opentelemetry::sdk::logs;

namespace
{
  [[maybe_unused]] std::shared_ptr<logs_sdk::LoggerProvider> g_logger_provider;

  void initLogger() {
    otlp::OtlpGrpcLogRecordExporterOptions loggerOptions;
    auto exporter  = otlp::OtlpGrpcLogRecordExporterFactory::Create(loggerOptions);
    auto processor = logs_sdk::SimpleLogRecordProcessorFactory::Create(std::move(exporter));
    std::vector<std::unique_ptr<logs_sdk::LogRecordProcessor>> processors;
    processors.push_back(std::move(processor));
    auto context = logs_sdk::LoggerContextFactory::Create(std::move(processors));
    std::shared_ptr<logs::LoggerProvider> provider = logs_sdk::LoggerProviderFactory::Create(std::move(context));
    g_logger_provider = std::static_pointer_cast<logs_sdk::LoggerProvider>(provider);
    opentelemetry::logs::Provider::SetLoggerProvider(provider);
  }

  void shutdownLogger()
  {
    if (g_logger_provider)
    {
      g_logger_provider->Shutdown();
      g_logger_provider.reset();
    }
  }

  nostd::shared_ptr<opentelemetry::logs::Logger> getLogger(std::string name){
    auto provider = logs::Provider::GetLoggerProvider();
    return provider->GetLogger(name + "_logger", name, OPENTELEMETRY_SDK_VERSION);
  }
}
