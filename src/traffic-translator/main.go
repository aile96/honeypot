package main

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/exporters/otlp/otlpmetric/otlpmetricgrpc"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracegrpc"
	otelmetric "go.opentelemetry.io/otel/metric"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/sdk/resource"
	sdkmetric "go.opentelemetry.io/otel/sdk/metric"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	semconv "go.opentelemetry.io/otel/semconv/v1.26.0"
	oteltrace "go.opentelemetry.io/otel/trace"
)

const serviceName = "traffic-translator"

// TranslateRequest is the JSON payload accepted by the relay.
type TranslateRequest struct {
	Target        string            `json:"target"`                    // e.g., payment.pay.svc.cluster.local:8081
	Method        string            `json:"method"`                    // e.g., oteldemo.PaymentService/ReceivePayment
	Payload       interface{}       `json:"payload,omitempty"`         // JSON request message
	Plaintext     *bool             `json:"plaintext,omitempty"`       // default: true (env DEFAULT_PLAINTEXT)
	Headers       map[string]string `json:"headers,omitempty"`         // -rpc-header
	TimeoutS      *int              `json:"timeout_s,omitempty"`       // default from env or 10
	ProtoFilesMap map[string]string `json:"proto_files_map,omitempty"` // {"demo.proto":"syntax = ..."}
	ProtosetB64   string            `json:"protoset_b64,omitempty"`    // base64 encoded descriptors.fds
	UseReflection *bool             `json:"use_reflection,omitempty"`  // default true (unless proto/protoset provided)
	CACert        string            `json:"cacert,omitempty"`
	Cert          string            `json:"cert,omitempty"`
	Key           string            `json:"key,omitempty"`
	Authority     string            `json:"authority,omitempty"`
}

// TranslateResponse is the JSON response from the relay.
type TranslateResponse struct {
	OK        bool            `json:"ok"`
	ExitCode  int             `json:"exit_code"`
	Stdout    json.RawMessage `json:"stdout,omitempty"`     // parsed JSON stdout (if possible)
	StdoutTxt string          `json:"stdout_txt,omitempty"` // raw stdout if not JSON
	Stderr    string          `json:"stderr,omitempty"`
	ElapsedMs int64           `json:"elapsed_ms"`
}

var (
	translatorTracer      = otel.Tracer(serviceName)
	httpRequestCounter    otelmetric.Int64Counter
	httpRequestDurationMs otelmetric.Float64Histogram
	grpcurlExecCounter    otelmetric.Int64Counter
	grpcurlExecErrors     otelmetric.Int64Counter
	grpcurlExecDurationMs otelmetric.Float64Histogram
)

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	shutdownTelemetry, err := initTelemetry(ctx)
	if err != nil {
		log.Printf("telemetry init failed, continuing without OTLP export: %v", err)
		shutdownTelemetry = func(context.Context) error { return nil }
	}
	initInstruments()

	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", healthzHandler)
	mux.HandleFunc("/translate", translateHandler)

	addr := ":" + getenvDefault("PORT", "8080")
	server := &http.Server{
		Addr:    addr,
		Handler: mux,
	}

	go func() {
		<-ctx.Done()
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if err := server.Shutdown(shutdownCtx); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Printf("http server shutdown error: %v", err)
		}
	}()

	log.Printf("http→gRPC relay listening on %s", addr)
	if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		log.Printf("http server error: %v", err)
	}

	shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := shutdownTelemetry(shutdownCtx); err != nil {
		log.Printf("telemetry shutdown error: %v", err)
	}
}

func healthzHandler(w http.ResponseWriter, r *http.Request) {
	ctx := otel.GetTextMapPropagator().Extract(r.Context(), propagation.HeaderCarrier(r.Header))
	start := time.Now()
	statusCode := http.StatusOK

	ctx, span := translatorTracer.Start(
		ctx,
		"traffic-translator.healthz",
		oteltrace.WithSpanKind(oteltrace.SpanKindServer),
		oteltrace.WithAttributes(
			attribute.String("http.method", r.Method),
			attribute.String("http.route", "/healthz"),
		),
	)
	defer func() {
		span.SetAttributes(attribute.Int("http.status_code", statusCode))
		recordHTTPMetrics(ctx, "/healthz", statusCode, time.Since(start))
		if statusCode >= http.StatusBadRequest {
			span.SetStatus(codes.Error, http.StatusText(statusCode))
		} else {
			span.SetStatus(codes.Ok, "")
		}
		span.End()
	}()

	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte("ok"))
}

func translateHandler(w http.ResponseWriter, r *http.Request) {
	ctx := otel.GetTextMapPropagator().Extract(r.Context(), propagation.HeaderCarrier(r.Header))
	start := time.Now()
	statusCode := http.StatusOK

	ctx, span := translatorTracer.Start(
		ctx,
		"traffic-translator.translate",
		oteltrace.WithSpanKind(oteltrace.SpanKindServer),
		oteltrace.WithAttributes(
			attribute.String("http.method", r.Method),
			attribute.String("http.route", "/translate"),
		),
	)
	defer func() {
		span.SetAttributes(attribute.Int("http.status_code", statusCode))
		recordHTTPMetrics(ctx, "/translate", statusCode, time.Since(start))
		if statusCode >= http.StatusBadRequest {
			span.SetStatus(codes.Error, http.StatusText(statusCode))
		} else {
			span.SetStatus(codes.Ok, "")
		}
		span.End()
	}()

	fail := func(code int, err error) {
		statusCode = code
		span.RecordError(err)
		span.SetStatus(codes.Error, err.Error())
		writeErr(w, code, err)
	}

	if r.Method != http.MethodPost {
		fail(http.StatusMethodNotAllowed, errors.New("use POST"))
		return
	}

	var req TranslateRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		fail(http.StatusBadRequest, fmt.Errorf("invalid json: %w", err))
		return
	}

	// basic validation
	if req.Target == "" || req.Method == "" {
		fail(http.StatusBadRequest, errors.New("missing target or method"))
		return
	}

	// Defaults
	if req.Plaintext == nil {
		def := true
		if v := os.Getenv("DEFAULT_PLAINTEXT"); v != "" {
			if v == "false" || v == "0" {
				def = false
			}
		}
		req.Plaintext = &def
	}
	timeout := 10
	if req.TimeoutS != nil && *req.TimeoutS > 0 {
		timeout = *req.TimeoutS
	} else if v := os.Getenv("DEFAULT_TIMEOUT_S"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 {
			timeout = n
		}
	}

	// serialize payload to JSON for -d
	var payloadBuf bytes.Buffer
	if req.Payload != nil {
		if err := json.NewEncoder(&payloadBuf).Encode(req.Payload); err != nil {
			fail(http.StatusBadRequest, fmt.Errorf("invalid payload json: %w", err))
			return
		}
	} else {
		payloadBuf.WriteString("{}")
	}

	span.SetAttributes(
		attribute.String("rpc.target", req.Target),
		attribute.String("rpc.method", req.Method),
		attribute.Bool("grpc.plaintext", *req.Plaintext),
		attribute.Int("grpc.timeout_s", timeout),
		attribute.Int("grpc.headers.count", len(req.Headers)),
		attribute.Int("grpc.proto_files.count", len(req.ProtoFilesMap)),
		attribute.Bool("grpc.protoset.provided", req.ProtosetB64 != ""),
	)

	// If proto files or protoset are provided -> create tempdir and write them there
	var tempDir string
	var cleanupTemp bool
	if len(req.ProtoFilesMap) > 0 || req.ProtosetB64 != "" {
		td, err := os.MkdirTemp("", "protos-")
		if err != nil {
			fail(http.StatusInternalServerError, fmt.Errorf("create tempdir: %w", err))
			return
		}
		tempDir = td
		cleanupTemp = true
		defer func() {
			if cleanupTemp {
				_ = os.RemoveAll(tempDir)
			}
		}()
	}

	// Write .proto files if provided
	for fname, content := range req.ProtoFilesMap {
		// simple basic sanitization: do not allow paths escaping the dir (strip any ../)
		clean := filepath.Clean(fname)
		if clean == "." || clean == ".." || clean == "/" || clean == "\\" || filepath.IsAbs(clean) {
			fail(http.StatusBadRequest, fmt.Errorf("invalid proto filename: %s", fname))
			return
		}
		target := filepath.Join(tempDir, clean)
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			fail(http.StatusInternalServerError, fmt.Errorf("mkdir proto dir: %w", err))
			return
		}
		if err := os.WriteFile(target, []byte(content), 0o644); err != nil {
			fail(http.StatusInternalServerError, fmt.Errorf("write proto %s: %w", fname, err))
			return
		}
	}

	// Write protoset if present
	var protosetPath string
	if req.ProtosetB64 != "" {
		data, err := base64.StdEncoding.DecodeString(req.ProtosetB64)
		if err != nil {
			fail(http.StatusBadRequest, fmt.Errorf("invalid base64 protoset: %w", err))
			return
		}
		protosetPath = filepath.Join(tempDir, "descriptors.fds")
		if err := os.WriteFile(protosetPath, data, 0o644); err != nil {
			fail(http.StatusInternalServerError, fmt.Errorf("write protoset: %w", err))
			return
		}
	}

	// Build args for grpcurl
	args := []string{
		"-format", "json",
		"-connect-timeout", "5",
		"-max-time", strconv.Itoa(timeout),
	}

	// TLS / Plaintext
	if *req.Plaintext {
		args = append(args, "-plaintext")
	} else {
		if req.CACert != "" {
			args = append(args, "-cacert", req.CACert)
		}
		if req.Cert != "" && req.Key != "" {
			args = append(args, "-cert", req.Cert, "-key", req.Key)
		}
		if req.Authority != "" {
			args = append(args, "-authority", req.Authority)
		}
	}

	// Headers (metadata)
	metadataHeaders := make(map[string]string, len(req.Headers)+3)
	for k, v := range req.Headers {
		metadataHeaders[k] = v
	}
	otel.GetTextMapPropagator().Inject(ctx, propagation.MapCarrier(metadataHeaders))
	for k, v := range metadataHeaders {
		args = append(args, "-rpc-header", k+": "+v)
	}

	// Handle proto/protoset/reflection
	if protosetPath != "" {
		args = append(args, "-protoset", protosetPath)
	} else if len(req.ProtoFilesMap) > 0 {
		// add import-path tempDir and -proto for each file
		args = append(args, "-import-path", tempDir)
		for fname := range req.ProtoFilesMap {
			p := filepath.Join(tempDir, filepath.Clean(fname))
			args = append(args, "-proto", p)
		}
	} else {
		// no proto provided: reflection by default. If the user asks to disable it, handle it
		useRef := true
		if req.UseReflection != nil {
			useRef = *req.UseReflection
		}
		if !useRef {
			args = append(args, "-use-reflection=false")
		}
	}

	// payload, target, method
	args = append(args, "-d", payloadBuf.String(), req.Target, req.Method)

	// Context with timeout
	ctx, cancel := context.WithTimeout(ctx, time.Duration(timeout+5)*time.Second)
	defer cancel()

	execCtx, execSpan := translatorTracer.Start(
		ctx,
		"traffic-translator.grpcurl.exec",
		oteltrace.WithSpanKind(oteltrace.SpanKindClient),
		oteltrace.WithAttributes(
			attribute.String("rpc.target", req.Target),
			attribute.String("rpc.method", req.Method),
			attribute.Int("grpcurl.args.count", len(args)),
		),
	)

	// Execute grpcurl
	cmd := exec.CommandContext(execCtx, "grpcurl", args...)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	execStart := time.Now()
	err := cmd.Run()
	elapsed := time.Since(execStart).Milliseconds()
	exitCode := exitCodeFromErr(err)
	addCounter(grpcurlExecCounter, execCtx, 1, attribute.Int("process.exit_code", exitCode))
	recordHistogram(grpcurlExecDurationMs, execCtx, float64(elapsed), attribute.Int("process.exit_code", exitCode))
	execSpan.SetAttributes(
		attribute.Int("process.exit_code", exitCode),
		attribute.Int64("grpcurl.elapsed_ms", elapsed),
	)
	if err != nil {
		addCounter(grpcurlExecErrors, execCtx, 1, attribute.Int("process.exit_code", exitCode))
		execSpan.RecordError(err)
		execSpan.SetStatus(codes.Error, err.Error())
	} else {
		execSpan.SetStatus(codes.Ok, "")
	}
	execSpan.End()

	resp := TranslateResponse{
		OK:        err == nil,
		ExitCode:  exitCode,
		Stderr:    stderr.String(),
		ElapsedMs: elapsed,
	}

	// Try to interpret stdout as JSON
	out := bytes.TrimSpace(stdout.Bytes())
	if len(out) > 0 && (out[0] == '{' || out[0] == '[' || out[0] == '"') {
		// valid JSON? If so, store as RawMessage
		resp.Stdout = json.RawMessage(out)
	} else if len(out) > 0 {
		resp.StdoutTxt = string(out)
	}

	code := http.StatusOK
	if !resp.OK {
		code = http.StatusBadGateway
	}
	statusCode = code

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	if err := json.NewEncoder(w).Encode(resp); err != nil {
		log.Printf("encode response error: %v", err)
	}
}

// writeErr sends a JSON error response
func writeErr(w http.ResponseWriter, code int, err error) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(map[string]any{
		"ok":    false,
		"error": err.Error(),
	})
}

// exitCodeFromErr extracts the exec exit code from the error, if possible
func exitCodeFromErr(err error) int {
	if err == nil {
		return 0
	}
	var ee *exec.ExitError
	if errors.As(err, &ee) {
		if status, ok := ee.Sys().(interface{ ExitStatus() int }); ok {
			return status.ExitStatus()
		}
		// Fall back to 1
		return 1
	}
	// if the command was not found or other issues
	return 1
}

func initTelemetry(ctx context.Context) (func(context.Context) error, error) {
	resourceAttrs, err := resource.Merge(
		resource.Default(),
		resource.NewWithAttributes(
			semconv.SchemaURL,
			semconv.ServiceName(serviceName),
		),
	)
	if err != nil {
		return nil, fmt.Errorf("resource setup: %w", err)
	}

	traceOpts := []otlptracegrpc.Option{}
	if endpoint := endpointFromEnv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "OTEL_EXPORTER_OTLP_ENDPOINT"); endpoint != "" {
		traceOpts = append(traceOpts, otlptracegrpc.WithEndpoint(endpoint))
	}
	if boolFromEnv("OTEL_EXPORTER_OTLP_INSECURE", true) {
		traceOpts = append(traceOpts, otlptracegrpc.WithInsecure())
	}
	traceExporter, err := otlptracegrpc.New(ctx, traceOpts...)
	if err != nil {
		return nil, fmt.Errorf("trace exporter setup: %w", err)
	}
	traceProvider := sdktrace.NewTracerProvider(
		sdktrace.WithResource(resourceAttrs),
		sdktrace.WithBatcher(traceExporter),
	)
	otel.SetTracerProvider(traceProvider)

	metricOpts := []otlpmetricgrpc.Option{}
	if endpoint := endpointFromEnv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", "OTEL_EXPORTER_OTLP_ENDPOINT"); endpoint != "" {
		metricOpts = append(metricOpts, otlpmetricgrpc.WithEndpoint(endpoint))
	}
	if boolFromEnv("OTEL_EXPORTER_OTLP_INSECURE", true) {
		metricOpts = append(metricOpts, otlpmetricgrpc.WithInsecure())
	}
	metricExporter, err := otlpmetricgrpc.New(ctx, metricOpts...)
	if err != nil {
		_ = traceProvider.Shutdown(ctx)
		return nil, fmt.Errorf("metric exporter setup: %w", err)
	}
	metricReader := sdkmetric.NewPeriodicReader(metricExporter)
	meterProvider := sdkmetric.NewMeterProvider(
		sdkmetric.WithResource(resourceAttrs),
		sdkmetric.WithReader(metricReader),
	)
	otel.SetMeterProvider(meterProvider)
	otel.SetTextMapPropagator(
		propagation.NewCompositeTextMapPropagator(
			propagation.TraceContext{},
			propagation.Baggage{},
		),
	)
	translatorTracer = otel.Tracer(serviceName)

	return func(shutdownCtx context.Context) error {
		var shutdownErr error
		if err := meterProvider.Shutdown(shutdownCtx); err != nil {
			shutdownErr = errors.Join(shutdownErr, fmt.Errorf("meter shutdown: %w", err))
		}
		if err := traceProvider.Shutdown(shutdownCtx); err != nil {
			shutdownErr = errors.Join(shutdownErr, fmt.Errorf("trace shutdown: %w", err))
		}
		return shutdownErr
	}, nil
}

func initInstruments() {
	meter := otel.GetMeterProvider().Meter(serviceName)
	var err error

	httpRequestCounter, err = meter.Int64Counter(
		"app.traffic_translator.http.requests",
		otelmetric.WithDescription("Number of HTTP requests received by route and status."),
	)
	if err != nil {
		log.Printf("metric setup error (http requests): %v", err)
	}

	httpRequestDurationMs, err = meter.Float64Histogram(
		"app.traffic_translator.http.duration",
		otelmetric.WithDescription("HTTP request latency in milliseconds."),
		otelmetric.WithUnit("ms"),
	)
	if err != nil {
		log.Printf("metric setup error (http duration): %v", err)
	}

	grpcurlExecCounter, err = meter.Int64Counter(
		"app.traffic_translator.grpcurl.executions",
		otelmetric.WithDescription("Number of grpcurl command executions."),
	)
	if err != nil {
		log.Printf("metric setup error (grpcurl executions): %v", err)
	}

	grpcurlExecErrors, err = meter.Int64Counter(
		"app.traffic_translator.grpcurl.errors",
		otelmetric.WithDescription("Number of grpcurl command failures."),
	)
	if err != nil {
		log.Printf("metric setup error (grpcurl errors): %v", err)
	}

	grpcurlExecDurationMs, err = meter.Float64Histogram(
		"app.traffic_translator.grpcurl.duration",
		otelmetric.WithDescription("grpcurl execution time in milliseconds."),
		otelmetric.WithUnit("ms"),
	)
	if err != nil {
		log.Printf("metric setup error (grpcurl duration): %v", err)
	}
}

func recordHTTPMetrics(ctx context.Context, route string, statusCode int, elapsed time.Duration) {
	attrs := []attribute.KeyValue{
		attribute.String("http.route", route),
		attribute.Int("http.status_code", statusCode),
	}
	addCounter(httpRequestCounter, ctx, 1, attrs...)
	recordHistogram(httpRequestDurationMs, ctx, float64(elapsed.Milliseconds()), attrs...)
}

func addCounter(counter otelmetric.Int64Counter, ctx context.Context, value int64, attrs ...attribute.KeyValue) {
	if counter == nil {
		return
	}
	counter.Add(ctx, value, otelmetric.WithAttributes(attrs...))
}

func recordHistogram(histogram otelmetric.Float64Histogram, ctx context.Context, value float64, attrs ...attribute.KeyValue) {
	if histogram == nil {
		return
	}
	histogram.Record(ctx, value, otelmetric.WithAttributes(attrs...))
}

func endpointFromEnv(keys ...string) string {
	for _, key := range keys {
		raw := strings.TrimSpace(os.Getenv(key))
		if raw == "" {
			continue
		}
		if parsed, err := url.Parse(raw); err == nil && parsed.Host != "" {
			return parsed.Host
		}
		cleaned := strings.TrimPrefix(strings.TrimPrefix(raw, "http://"), "https://")
		cleaned = strings.TrimSuffix(cleaned, "/")
		if idx := strings.Index(cleaned, "/"); idx > 0 {
			cleaned = cleaned[:idx]
		}
		if cleaned != "" {
			return cleaned
		}
	}
	return ""
}

func boolFromEnv(key string, def bool) bool {
	raw := strings.TrimSpace(strings.ToLower(os.Getenv(key)))
	if raw == "" {
		return def
	}
	switch raw {
	case "1", "true", "yes", "on":
		return true
	case "0", "false", "no", "off":
		return false
	default:
		return def
	}
}

// getenvDefault returns the env value or the default
func getenvDefault(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}
