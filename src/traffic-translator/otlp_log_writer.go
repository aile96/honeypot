// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
package main

import (
	"context"
	"fmt"
	"os"
	"strings"
	"sync"
	"time"

	commonpb "go.opentelemetry.io/proto/otlp/common/v1"
	collogspb "go.opentelemetry.io/proto/otlp/collector/logs/v1"
	logspb "go.opentelemetry.io/proto/otlp/logs/v1"
	resourcepb "go.opentelemetry.io/proto/otlp/resource/v1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

const trafficTranslatorLogsBufferSize = 256

type OTLPLogWriter struct {
	client        collogspb.LogsServiceClient
	conn          *grpc.ClientConn
	resourceAttrs []*commonpb.KeyValue
	scopeName     string
	records       chan *logspb.LogRecord
	wg            sync.WaitGroup
}

func NewOTLPLogWriter(ctx context.Context, serviceName, scopeName string) (*OTLPLogWriter, error) {
	endpoint := endpointFromEnv("OTEL_EXPORTER_OTLP_ENDPOINT")
	if endpoint == "" {
		endpoint = "otel-collector:4317"
	}

	if serviceName == "" {
		serviceName = "traffic-translator"
	}
	if scopeName == "" {
		scopeName = "traffic-translator.stdlog"
	}

	conn, err := grpc.DialContext(ctx, endpoint, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		return nil, fmt.Errorf("dial OTLP logs endpoint %q: %w", endpoint, err)
	}

	writer := &OTLPLogWriter{
		client: collogspb.NewLogsServiceClient(conn),
		conn:   conn,
		resourceAttrs: []*commonpb.KeyValue{
			{
				Key: "service.name",
				Value: &commonpb.AnyValue{
					Value: &commonpb.AnyValue_StringValue{StringValue: serviceName},
				},
			},
		},
		scopeName: scopeName,
		records:   make(chan *logspb.LogRecord, trafficTranslatorLogsBufferSize),
	}

	writer.wg.Add(1)
	go writer.run()
	return writer, nil
}

func (w *OTLPLogWriter) Write(p []byte) (n int, err error) {
	message := strings.TrimSpace(string(p))
	if message == "" {
		return len(p), nil
	}

	now := time.Now()
	record := &logspb.LogRecord{
		TimeUnixNano:         uint64(now.UnixNano()),
		ObservedTimeUnixNano: uint64(now.UnixNano()),
		SeverityNumber:       severityFromMessage(message),
		SeverityText:         strings.ToUpper(severityTextFromMessage(message)),
		Body: &commonpb.AnyValue{
			Value: &commonpb.AnyValue_StringValue{StringValue: message},
		},
	}

	select {
	case w.records <- record:
	default:
		fmt.Fprintln(os.Stderr, "[traffic-translator] dropping OTLP log record because buffer is full")
	}

	return len(p), nil
}

func (w *OTLPLogWriter) Close(ctx context.Context) error {
	close(w.records)

	waitCh := make(chan struct{})
	go func() {
		w.wg.Wait()
		close(waitCh)
	}()

	select {
	case <-waitCh:
	case <-ctx.Done():
		return ctx.Err()
	}

	return w.conn.Close()
}

func (w *OTLPLogWriter) run() {
	defer w.wg.Done()

	for record := range w.records {
		req := &collogspb.ExportLogsServiceRequest{
			ResourceLogs: []*logspb.ResourceLogs{
				{
					Resource: &resourcepb.Resource{Attributes: w.resourceAttrs},
					ScopeLogs: []*logspb.ScopeLogs{
						{
							Scope:      &commonpb.InstrumentationScope{Name: w.scopeName},
							LogRecords: []*logspb.LogRecord{record},
						},
					},
				},
			},
		}

		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		_, err := w.client.Export(ctx, req)
		cancel()
		if err != nil {
			fmt.Fprintf(os.Stderr, "[traffic-translator] OTLP logs export failed: %v\n", err)
		}
	}
}

func severityTextFromMessage(message string) string {
	lower := strings.ToLower(message)
	switch {
	case strings.Contains(lower, "panic"), strings.Contains(lower, "fatal"):
		return "fatal"
	case strings.Contains(lower, "error"), strings.Contains(lower, "failed"):
		return "error"
	case strings.Contains(lower, "warn"):
		return "warn"
	case strings.Contains(lower, "debug"):
		return "debug"
	default:
		return "info"
	}
}

func severityFromMessage(message string) logspb.SeverityNumber {
	switch severityTextFromMessage(message) {
	case "fatal":
		return logspb.SeverityNumber(21)
	case "error":
		return logspb.SeverityNumber(17)
	case "warn":
		return logspb.SeverityNumber(13)
	case "debug":
		return logspb.SeverityNumber(5)
	default:
		return logspb.SeverityNumber(9)
	}
}
