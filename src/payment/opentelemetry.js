// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

const opentelemetry = require("@opentelemetry/sdk-node")
const {getNodeAutoInstrumentations} = require("@opentelemetry/auto-instrumentations-node")
const {OTLPTraceExporter} = require('@opentelemetry/exporter-trace-otlp-grpc')
const {OTLPMetricExporter} = require('@opentelemetry/exporter-metrics-otlp-grpc')
const {OTLPLogExporter} = require('@opentelemetry/exporter-logs-otlp-grpc')
const {PeriodicExportingMetricReader} = require('@opentelemetry/sdk-metrics')
const {BatchLogRecordProcessor} = require('@opentelemetry/sdk-logs')
const {alibabaCloudEcsDetector} = require('@opentelemetry/resource-detector-alibaba-cloud')
const {awsEc2Detector, awsEksDetector} = require('@opentelemetry/resource-detector-aws')
const {containerDetector} = require('@opentelemetry/resource-detector-container')
const {gcpDetector} = require('@opentelemetry/resource-detector-gcp')
const {envDetector, hostDetector, osDetector, processDetector} = require('@opentelemetry/resources')
const {RuntimeNodeInstrumentation} = require('@opentelemetry/instrumentation-runtime-node')

const otlpLogProcessor = new BatchLogRecordProcessor(new OTLPLogExporter())

const sdk = new opentelemetry.NodeSDK({
  traceExporter: new OTLPTraceExporter(),
  logRecordProcessor: otlpLogProcessor,
  logRecordProcessors: [otlpLogProcessor],
  instrumentations: [
    getNodeAutoInstrumentations({
      // only instrument fs if it is part of another trace
      '@opentelemetry/instrumentation-fs': {
        requireParentSpan: true,
      },
      '@opentelemetry/instrumentation-pino': {
        enabled: true,
      },
    }),
    new RuntimeNodeInstrumentation({
      monitoringPrecision: 5000,
    })
  ],
  metricReader: new PeriodicExportingMetricReader({
    exporter: new OTLPMetricExporter()
  }),
  resourceDetectors: [
    containerDetector,
    envDetector,
    hostDetector,
    osDetector,
    processDetector,
    alibabaCloudEcsDetector,
    awsEksDetector,
    awsEc2Detector,
    gcpDetector
  ],
})

sdk.start();

const shutdown = (signal) => {
  sdk.shutdown()
    .catch((err) => {
      console.error(`[otel] error during ${signal} shutdown`, err)
    })
    .finally(() => {
      process.exit(0)
    })
}

process.once('SIGTERM', () => shutdown('SIGTERM'))
process.once('SIGINT', () => shutdown('SIGINT'))
