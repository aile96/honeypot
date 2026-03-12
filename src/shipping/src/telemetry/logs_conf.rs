// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

use log::{Level, LevelFilter, Log, Metadata, Record};
use std::env;
use std::fmt::Write as _;
use std::io::Write;
use std::net::TcpStream;
use std::sync::mpsc::{sync_channel, Receiver, SyncSender};
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

const LOG_BUFFER_SIZE: usize = 512;

struct HttpEndpoint {
    host: String,
    port: u16,
    path: String,
}

struct OtlpLogger {
    sender: SyncSender<String>,
    level: LevelFilter,
}

impl Log for OtlpLogger {
    fn enabled(&self, metadata: &Metadata) -> bool {
        metadata.level().to_level_filter() <= self.level
    }

    fn log(&self, record: &Record) {
        if !self.enabled(record.metadata()) {
            return;
        }

        let message = record.args().to_string();
        let severity = severity(record.level());
        let now_nano = time_nanos();
        let escaped = escape_json(&message);

        let payload = format!(
            "{{\"resourceLogs\":[{{\"resource\":{{\"attributes\":[{{\"key\":\"service.name\",\"value\":{{\"stringValue\":\"shipping\"}}}}]}},\"scopeLogs\":[{{\"scope\":{{\"name\":\"shipping.log\"}},\"logRecords\":[{{\"timeUnixNano\":\"{now_nano}\",\"observedTimeUnixNano\":\"{now_nano}\",\"severityText\":\"{severity_text}\",\"severityNumber\":{severity_number},\"body\":{{\"stringValue\":\"{escaped}\"}}}}]}}]}}]}}",
            severity_text = severity.0,
            severity_number = severity.1,
        );

        let _ = self.sender.try_send(payload);
    }

    fn flush(&self) {}
}

pub fn init_logger() -> Result<(), log::SetLoggerError> {
    let endpoint = resolve_otlp_logs_endpoint();
    let (tx, rx) = sync_channel::<String>(LOG_BUFFER_SIZE);
    spawn_export_worker(endpoint, rx);

    let logger = Box::new(OtlpLogger {
        sender: tx,
        level: LevelFilter::Info,
    });
    let logger_ref: &'static OtlpLogger = Box::leak(logger);
    log::set_logger(logger_ref)?;
    log::set_max_level(LevelFilter::Info);
    Ok(())
}

fn spawn_export_worker(endpoint: HttpEndpoint, rx: Receiver<String>) {
    thread::spawn(move || {
        for payload in rx {
            let _ = post_otlp_log(&endpoint, &payload);
        }
    });
}

fn post_otlp_log(endpoint: &HttpEndpoint, payload: &str) -> std::io::Result<()> {
    let mut stream = TcpStream::connect((endpoint.host.as_str(), endpoint.port))?;
    stream.set_write_timeout(Some(Duration::from_secs(2)))?;
    stream.set_read_timeout(Some(Duration::from_secs(2)))?;

    let mut request = String::new();
    let _ = write!(
        request,
        "POST {} HTTP/1.1\r\nHost: {}:{}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
        endpoint.path,
        endpoint.host,
        endpoint.port,
        payload.len(),
        payload
    );

    stream.write_all(request.as_bytes())
}

fn resolve_otlp_logs_endpoint() -> HttpEndpoint {
    if let Some(endpoint) = env_var("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT") {
        return parse_http_endpoint(&endpoint);
    }

    if let Some(base) = env_var("OTEL_EXPORTER_OTLP_ENDPOINT") {
        let normalized = if base.ends_with(":4317") {
            base.trim_end_matches(":4317").to_string() + ":4318"
        } else {
            base
        };
        return parse_http_endpoint(&(normalized.trim_end_matches('/').to_string() + "/v1/logs"));
    }

    parse_http_endpoint("http://otel-collector:4318/v1/logs")
}

fn parse_http_endpoint(value: &str) -> HttpEndpoint {
    let mut raw = value.trim().to_string();
    if !raw.starts_with("http://") && !raw.starts_with("https://") {
        raw = format!("http://{raw}");
    }

    let no_scheme = raw
        .trim_start_matches("http://")
        .trim_start_matches("https://")
        .to_string();

    let (host_port, path_part) = match no_scheme.split_once('/') {
        Some((hp, p)) => (hp, format!("/{p}")),
        None => (no_scheme.as_str(), "/v1/logs".to_string()),
    };

    let (host, port) = match host_port.rsplit_once(':') {
        Some((h, p)) => (h.to_string(), p.parse::<u16>().unwrap_or(4318)),
        None => (host_port.to_string(), 4318),
    };

    let path = if path_part.is_empty() {
        "/v1/logs".to_string()
    } else {
        path_part
    };

    HttpEndpoint { host, port, path }
}

fn env_var(key: &str) -> Option<String> {
    env::var(key).ok().map(|v| v.trim().to_string()).filter(|v| !v.is_empty())
}

fn time_nanos() -> u128 {
    match SystemTime::now().duration_since(UNIX_EPOCH) {
        Ok(duration) => duration.as_nanos(),
        Err(_) => 0,
    }
}

fn escape_json(value: &str) -> String {
    value
        .replace('\\', "\\\\")
        .replace('"', "\\\"")
        .replace('\n', "\\n")
        .replace('\r', "\\r")
}

fn severity(level: Level) -> (&'static str, u8) {
    match level {
        Level::Error => ("ERROR", 17),
        Level::Warn => ("WARN", 13),
        Level::Info => ("INFO", 9),
        Level::Debug => ("DEBUG", 5),
        Level::Trace => ("TRACE", 1),
    }
}
