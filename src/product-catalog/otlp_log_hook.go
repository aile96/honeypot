// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
package main

import (
	"context"
	"fmt"
	"net/url"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/sirupsen/logrus"
	commonpb "go.opentelemetry.io/proto/otlp/common/v1"
	collogspb "go.opentelemetry.io/proto/otlp/collector/logs/v1"
	logspb "go.opentelemetry.io/proto/otlp/logs/v1"
	resourcepb "go.opentelemetry.io/proto/otlp/resource/v1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

const productCatalogLogsBufferSize = 256

type OTLPLogHook struct {
	client        collogspb.LogsServiceClient
	conn          *grpc.ClientConn
	resourceAttrs []*commonpb.KeyValue
	scopeName     string
	records       chan *logspb.LogRecord
	wg            sync.WaitGroup
}

func NewOTLPLogHook(ctx context.Context, serviceName string, scopeName string) (*OTLPLogHook, error) {
	endpoint := resolveOTLPGRPCEndpoint()
	if endpoint == "" {
		return nil, fmt.Errorf("OTEL_EXPORTER_OTLP_ENDPOINT is not set")
	}
	if serviceName == "" {
		serviceName = "product-catalog"
	}
	if scopeName == "" {
		scopeName = "product-catalog.logrus"
	}

	conn, err := grpc.DialContext(ctx, endpoint, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		return nil, fmt.Errorf("dial OTLP logs endpoint %q: %w", endpoint, err)
	}

	hook := &OTLPLogHook{
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
		records:   make(chan *logspb.LogRecord, productCatalogLogsBufferSize),
	}

	hook.wg.Add(1)
	go hook.run()
	return hook, nil
}

func (h *OTLPLogHook) Levels() []logrus.Level {
	return logrus.AllLevels
}

func (h *OTLPLogHook) Fire(entry *logrus.Entry) error {
	if h == nil {
		return nil
	}

	attrs := make([]*commonpb.KeyValue, 0, len(entry.Data))
	for key, value := range entry.Data {
		attrs = append(attrs, &commonpb.KeyValue{
			Key:   key,
			Value: toAnyValue(value),
		})
	}

	record := &logspb.LogRecord{
		TimeUnixNano:         uint64(entry.Time.UnixNano()),
		ObservedTimeUnixNano: uint64(time.Now().UnixNano()),
		SeverityNumber:       mapSeverity(entry.Level),
		SeverityText:         strings.ToUpper(entry.Level.String()),
		Body: &commonpb.AnyValue{
			Value: &commonpb.AnyValue_StringValue{StringValue: entry.Message},
		},
		Attributes: attrs,
	}

	select {
	case h.records <- record:
	default:
		fmt.Fprintln(os.Stderr, "[product-catalog] dropping OTLP log record because buffer is full")
	}

	return nil
}

func (h *OTLPLogHook) Close(ctx context.Context) error {
	if h == nil {
		return nil
	}

	close(h.records)

	waitCh := make(chan struct{})
	go func() {
		h.wg.Wait()
		close(waitCh)
	}()

	select {
	case <-waitCh:
	case <-ctx.Done():
		return ctx.Err()
	}

	return h.conn.Close()
}

func (h *OTLPLogHook) run() {
	defer h.wg.Done()

	for record := range h.records {
		req := &collogspb.ExportLogsServiceRequest{
			ResourceLogs: []*logspb.ResourceLogs{
				{
					Resource: &resourcepb.Resource{Attributes: h.resourceAttrs},
					ScopeLogs: []*logspb.ScopeLogs{
						{
							Scope:      &commonpb.InstrumentationScope{Name: h.scopeName},
							LogRecords: []*logspb.LogRecord{record},
						},
					},
				},
			},
		}

		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		_, err := h.client.Export(ctx, req)
		cancel()
		if err != nil {
			fmt.Fprintf(os.Stderr, "[product-catalog] OTLP logs export failed: %v\n", err)
		}
	}
}

func mapSeverity(level logrus.Level) logspb.SeverityNumber {
	switch level {
	case logrus.PanicLevel, logrus.FatalLevel:
		return logspb.SeverityNumber(21)
	case logrus.ErrorLevel:
		return logspb.SeverityNumber(17)
	case logrus.WarnLevel:
		return logspb.SeverityNumber(13)
	case logrus.InfoLevel:
		return logspb.SeverityNumber(9)
	case logrus.DebugLevel:
		return logspb.SeverityNumber(5)
	default:
		return logspb.SeverityNumber(1)
	}
}

func toAnyValue(value any) *commonpb.AnyValue {
	switch v := value.(type) {
	case string:
		return &commonpb.AnyValue{Value: &commonpb.AnyValue_StringValue{StringValue: v}}
	case bool:
		return &commonpb.AnyValue{Value: &commonpb.AnyValue_BoolValue{BoolValue: v}}
	case int:
		return &commonpb.AnyValue{Value: &commonpb.AnyValue_IntValue{IntValue: int64(v)}}
	case int8:
		return &commonpb.AnyValue{Value: &commonpb.AnyValue_IntValue{IntValue: int64(v)}}
	case int16:
		return &commonpb.AnyValue{Value: &commonpb.AnyValue_IntValue{IntValue: int64(v)}}
	case int32:
		return &commonpb.AnyValue{Value: &commonpb.AnyValue_IntValue{IntValue: int64(v)}}
	case int64:
		return &commonpb.AnyValue{Value: &commonpb.AnyValue_IntValue{IntValue: v}}
	case uint:
		return &commonpb.AnyValue{Value: &commonpb.AnyValue_IntValue{IntValue: int64(v)}}
	case uint8:
		return &commonpb.AnyValue{Value: &commonpb.AnyValue_IntValue{IntValue: int64(v)}}
	case uint16:
		return &commonpb.AnyValue{Value: &commonpb.AnyValue_IntValue{IntValue: int64(v)}}
	case uint32:
		return &commonpb.AnyValue{Value: &commonpb.AnyValue_IntValue{IntValue: int64(v)}}
	case uint64:
		return &commonpb.AnyValue{Value: &commonpb.AnyValue_IntValue{IntValue: int64(v)}}
	case float32:
		return &commonpb.AnyValue{Value: &commonpb.AnyValue_DoubleValue{DoubleValue: float64(v)}}
	case float64:
		return &commonpb.AnyValue{Value: &commonpb.AnyValue_DoubleValue{DoubleValue: v}}
	case time.Time:
		return &commonpb.AnyValue{Value: &commonpb.AnyValue_StringValue{StringValue: v.UTC().Format(time.RFC3339Nano)}}
	case fmt.Stringer:
		return &commonpb.AnyValue{Value: &commonpb.AnyValue_StringValue{StringValue: v.String()}}
	case error:
		return &commonpb.AnyValue{Value: &commonpb.AnyValue_StringValue{StringValue: v.Error()}}
	default:
		return &commonpb.AnyValue{
			Value: &commonpb.AnyValue_StringValue{
				StringValue: fmt.Sprintf("%v", value),
			},
		}
	}
}

func resolveOTLPGRPCEndpoint() string {
	raw := strings.TrimSpace(os.Getenv("OTEL_EXPORTER_OTLP_ENDPOINT"))
	if raw == "" {
		return "otel-collector:4317"
	}

	if parsed, err := url.Parse(raw); err == nil && parsed.Host != "" {
		return parsed.Host
	}

	clean := strings.TrimPrefix(strings.TrimPrefix(raw, "http://"), "https://")
	clean = strings.TrimSuffix(clean, "/")
	if idx := strings.Index(clean, "/"); idx > 0 {
		clean = clean[:idx]
	}
	if _, _, err := netSplitHostPort(clean); err == nil {
		return clean
	}
	if strings.Contains(clean, ":") {
		return clean
	}
	return clean + ":4317"
}

func netSplitHostPort(value string) (string, string, error) {
	host, port, ok := strings.Cut(value, ":")
	if !ok {
		return "", "", fmt.Errorf("missing port")
	}
	if host == "" || port == "" {
		return "", "", fmt.Errorf("invalid host:port")
	}
	if _, convErr := strconv.Atoi(port); convErr != nil {
		return "", "", convErr
	}
	return host, port, nil
}
