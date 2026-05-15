// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
import { registerOTel } from "@vercel/otel";

const PATCH_FLAG = "__flagd_ui_otlp_console_patched__";

function resolveLogsEndpoint(): string | null {
  const direct = process.env.OTEL_EXPORTER_OTLP_LOGS_ENDPOINT?.trim();
  if (direct) {
    return direct;
  }

  const base = process.env.OTEL_EXPORTER_OTLP_ENDPOINT?.trim();
  if (!base) {
    return null;
  }

  const normalized = base.endsWith("/") ? base.slice(0, -1) : base;
  if (normalized.endsWith(":4317")) {
    return `${normalized.slice(0, -5)}:4318/v1/logs`;
  }
  if (normalized.endsWith("/v1/logs")) {
    return normalized;
  }
  return `${normalized}/v1/logs`;
}

function severityToNumber(severity: "INFO" | "WARN" | "ERROR"): number {
  switch (severity) {
    case "ERROR":
      return 17;
    case "WARN":
      return 13;
    default:
      return 9;
  }
}

function safeToString(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function emitOtlpLog(
  severity: "INFO" | "WARN" | "ERROR",
  message: string,
  attributes: Record<string, string>,
): void {
  const endpoint = resolveLogsEndpoint();
  if (!endpoint) {
    return;
  }

  const serviceName = process.env.OTEL_SERVICE_NAME || "flagd-ui";
  const now = (BigInt(Date.now()) * 1_000_000n).toString();
  const attrList = Object.entries(attributes).map(([key, value]) => ({
    key,
    value: { stringValue: value },
  }));

  const payload = {
    resourceLogs: [
      {
        resource: {
          attributes: [{ key: "service.name", value: { stringValue: serviceName } }],
        },
        scopeLogs: [
          {
            scope: { name: "flagd-ui.console" },
            logRecords: [
              {
                timeUnixNano: now,
                observedTimeUnixNano: now,
                severityText: severity,
                severityNumber: severityToNumber(severity),
                body: { stringValue: message },
                attributes: attrList,
              },
            ],
          },
        ],
      },
    ],
  };

  void fetch(endpoint, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  }).catch(() => {});
}

function patchConsoleForOtlpLogs(): void {
  const globalObj = globalThis as Record<string, unknown>;
  if (globalObj[PATCH_FLAG]) {
    return;
  }
  globalObj[PATCH_FLAG] = true;

  const originalLog = console.log.bind(console);
  const originalInfo = console.info.bind(console);
  const originalWarn = console.warn.bind(console);
  const originalError = console.error.bind(console);

  const wrap =
    (
      original: (...args: unknown[]) => void,
      methodName: "log" | "info" | "warn" | "error",
      severity: "INFO" | "WARN" | "ERROR",
    ) =>
    (...args: unknown[]) => {
      original(...args);
      emitOtlpLog(severity, args.map(safeToString).join(" "), {
        "log.origin": "console",
        "log.method": methodName,
      });
    };

  console.log = wrap(originalLog, "log", "INFO");
  console.info = wrap(originalInfo, "info", "INFO");
  console.warn = wrap(originalWarn, "warn", "WARN");
  console.error = wrap(originalError, "error", "ERROR");
}

export function register() {
  registerOTel({ serviceName: "flagd-ui" });
  patchConsoleForOtlpLogs();
}
