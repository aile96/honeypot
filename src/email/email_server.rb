# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

require "ostruct"
require "pony"
require "sinatra"
require "logger"
require "json"
require "time"
require "net/http"
require "uri"

require "opentelemetry/sdk"
require "opentelemetry/exporter/otlp"
require "opentelemetry/instrumentation/sinatra"

set :port, ENV["EMAIL_PORT"]

def resolve_otlp_logs_endpoint
  explicit = ENV["OTEL_EXPORTER_OTLP_LOGS_ENDPOINT"].to_s.strip
  return explicit unless explicit.empty?

  base = ENV["OTEL_EXPORTER_OTLP_ENDPOINT"].to_s.strip
  return nil if base.empty?

  normalized = base.sub(%r{/*$}, "")
  normalized = normalized.sub(":4317", ":4318")
  "#{normalized}/v1/logs"
end

OTLP_LOGS_URI = begin
  endpoint = resolve_otlp_logs_endpoint
  endpoint && !endpoint.empty? ? URI.parse(endpoint) : nil
rescue URI::InvalidURIError
  nil
end

def severity_number(severity)
  case severity
  when "DEBUG" then 5
  when "INFO" then 9
  when "WARN" then 13
  when "ERROR" then 17
  when "FATAL" then 21
  else 9
  end
end

def otlp_attributes(payload)
  payload.each_with_object([]) do |(key, value), attrs|
    next if key == :timestamp || key == :severity
    attrs << {
      key: key.to_s,
      value: {
        stringValue: value.to_s,
      },
    }
  end
end

def emit_otlp_log(severity, payload)
  return if OTLP_LOGS_URI.nil?

  now_nano = (Time.now.to_r * 1_000_000_000).to_i.to_s
  service_name = ENV.fetch("OTEL_SERVICE_NAME", "email")
  body_text = payload[:message].to_s

  export_body = {
    resourceLogs: [
      {
        resource: {
          attributes: [
            { key: "service.name", value: { stringValue: service_name } },
          ],
        },
        scopeLogs: [
          {
            scope: { name: "email.logger" },
            logRecords: [
              {
                timeUnixNano: now_nano,
                observedTimeUnixNano: now_nano,
                severityText: severity,
                severityNumber: severity_number(severity),
                body: { stringValue: body_text },
                attributes: otlp_attributes(payload),
              },
            ],
          },
        ],
      },
    ],
  }

  req = Net::HTTP::Post.new(OTLP_LOGS_URI)
  req["Content-Type"] = "application/json"
  req.body = JSON.generate(export_body)

  http = Net::HTTP.new(OTLP_LOGS_URI.host, OTLP_LOGS_URI.port)
  http.use_ssl = OTLP_LOGS_URI.scheme == "https"
  http.open_timeout = 1
  http.read_timeout = 2
  http.request(req)
rescue StandardError => e
  $stderr.puts("[email] OTLP log export failed: #{e.class}: #{e.message}")
end

LOGGER = Logger.new($stdout)
LOGGER.level = Logger::INFO
LOGGER.formatter = proc do |severity, datetime, _progname, msg|
  payload = msg.is_a?(Hash) ? msg.dup : { message: msg.to_s }
  payload[:timestamp] = datetime.utc.iso8601
  payload[:severity] = severity
  payload[:"service.name"] = ENV.fetch("OTEL_SERVICE_NAME", "email")
  emit_otlp_log(severity, payload)
  "#{payload.to_json}\n"
end

OpenTelemetry::SDK.configure do |c|
  c.use "OpenTelemetry::Instrumentation::Sinatra"
end

post "/send_order_confirmation" do
  data = JSON.parse(request.body.read, object_class: OpenStruct)
  LOGGER.info(
    step: "send_order_confirmation.received",
    order_id: data.order&.order_id,
    email_hint: mask_email(data.email)
  )

  # get the current auto-instrumented span
  current_span = OpenTelemetry::Trace.current_span
  current_span.add_attributes({
    "app.order.id" => data.order.order_id,
  })

  send_email(data)

end

error do
  LOGGER.error(
    step: "send_order_confirmation.failed",
    error: env["sinatra.error"]&.message
  )
  OpenTelemetry::Trace.current_span.record_exception(env['sinatra.error'])
end

def send_email(data)
  # create and start a manual span
  tracer = OpenTelemetry.tracer_provider.tracer('email')
  tracer.in_span("send_email") do |span|
    Pony.mail(
      to:       data.email,
      from:     "noreply@example.com",
      subject:  "Your confirmation email",
      body:     erb(:confirmation, locals: { order: data.order }),
      via:      :test
    )
    span.set_attribute("app.email.recipient", data.email)
    LOGGER.info(
      step: "send_order_confirmation.sent",
      order_id: data.order&.order_id,
      email_hint: mask_email(data.email)
    )
  end
  # manually created spans need to be ended
  # in Ruby, the method `in_span` ends it automatically
  # check out the OpenTelemetry Ruby docs at: 
  # https://opentelemetry.io/docs/instrumentation/ruby/manual/#creating-new-spans 
end

def mask_email(email)
  parts = email.to_s.split("@", 2)
  return "unknown" if parts.length != 2 || parts[0].empty? || parts[1].empty?

  local = parts[0]
  shown = local.length <= 2 ? "***" : "#{local[0..1]}***"
  "#{shown}@#{parts[1]}"
end
