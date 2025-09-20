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
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"time"
)

// TranslateRequest è il payload JSON che il relay accetta.
type TranslateRequest struct {
	Target        string            `json:"target"`                    // es: payment.pay.svc.cluster.local:8081
	Method        string            `json:"method"`                    // es: oteldemo.PaymentService/ReceivePayment
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

// TranslateResponse è la risposta JSON del relay.
type TranslateResponse struct {
	OK        bool            `json:"ok"`
	ExitCode  int             `json:"exit_code"`
	Stdout    json.RawMessage `json:"stdout,omitempty"`   // parsed JSON stdout (se possibile)
	StdoutTxt string          `json:"stdout_txt,omitempty"` // raw stdout if not JSON
	Stderr    string          `json:"stderr,omitempty"`
	ElapsedMs int64           `json:"elapsed_ms"`
}

func main() {
	http.HandleFunc("/healthz", healthzHandler)
	http.HandleFunc("/translate", translateHandler)

	addr := ":" + getenvDefault("PORT", "8080")
	log.Printf("http→gRPC relay listening on %s", addr)
	log.Fatal(http.ListenAndServe(addr, nil))
}

func healthzHandler(w http.ResponseWriter, r *http.Request) {
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte("ok"))
}

func translateHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeErr(w, http.StatusMethodNotAllowed, errors.New("use POST"))
		return
	}

	var req TranslateRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeErr(w, http.StatusBadRequest, fmt.Errorf("invalid json: %w", err))
		return
	}

	// basic validation
	if req.Target == "" || req.Method == "" {
		writeErr(w, http.StatusBadRequest, errors.New("missing target or method"))
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

	// serializza payload in JSON per -d
	var payloadBuf bytes.Buffer
	if req.Payload != nil {
		if err := json.NewEncoder(&payloadBuf).Encode(req.Payload); err != nil {
			writeErr(w, http.StatusBadRequest, fmt.Errorf("invalid payload json: %w", err))
			return
		}
	} else {
		payloadBuf.WriteString("{}")
	}

	// Se sono stati forniti proto files o protoset -> crea tempdir e scrivi lì
	var tempDir string
	var cleanupTemp bool
	if len(req.ProtoFilesMap) > 0 || req.ProtosetB64 != "" {
		td, err := os.MkdirTemp("", "protos-")
		if err != nil {
			writeErr(w, http.StatusInternalServerError, fmt.Errorf("create tempdir: %w", err))
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

	// Scrivi i .proto se forniti
	for fname, content := range req.ProtoFilesMap {
		// semplice sanificazione base: non permetti path che escano dalla dir (rimuovi eventuali ../)
		clean := filepath.Clean(fname)
		if clean == "." || clean == ".." || clean == "/" || clean == "\\" || filepath.IsAbs(clean) {
			writeErr(w, http.StatusBadRequest, fmt.Errorf("invalid proto filename: %s", fname))
			return
		}
		target := filepath.Join(tempDir, clean)
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			writeErr(w, http.StatusInternalServerError, fmt.Errorf("mkdir proto dir: %w", err))
			return
		}
		if err := os.WriteFile(target, []byte(content), 0o644); err != nil {
			writeErr(w, http.StatusInternalServerError, fmt.Errorf("write proto %s: %w", fname, err))
			return
		}
	}

	// Scrivi protoset se presente
	var protosetPath string
	if req.ProtosetB64 != "" {
		data, err := base64.StdEncoding.DecodeString(req.ProtosetB64)
		if err != nil {
			writeErr(w, http.StatusBadRequest, fmt.Errorf("invalid base64 protoset: %w", err))
			return
		}
		protosetPath = filepath.Join(tempDir, "descriptors.fds")
		if err := os.WriteFile(protosetPath, data, 0o644); err != nil {
			writeErr(w, http.StatusInternalServerError, fmt.Errorf("write protoset: %w", err))
			return
		}
	}

	// Costruisci args per grpcurl
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
	for k, v := range req.Headers {
		args = append(args, "-rpc-header", k+": "+v)
	}

	// Gestione proto/protoset/reflection
	if protosetPath != "" {
		args = append(args, "-protoset", protosetPath)
	} else if len(req.ProtoFilesMap) > 0 {
		// aggiungi import-path tempDir e -proto per ogni file
		args = append(args, "-import-path", tempDir)
		for fname := range req.ProtoFilesMap {
			p := filepath.Join(tempDir, filepath.Clean(fname))
			args = append(args, "-proto", p)
		}
	} else {
		// nessun proto fornito: reflection per default. Se user chiede disabilitazione, gestiscila
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

	// Contesto con timeout
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(timeout+5)*time.Second)
	defer cancel()

	// Esegui grpcurl
	cmd := exec.CommandContext(ctx, "grpcurl", args...)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	start := time.Now()
	err := cmd.Run()
	elapsed := time.Since(start).Milliseconds()

	resp := TranslateResponse{
		OK:        err == nil,
		ExitCode:  exitCodeFromErr(err),
		Stderr:    stderr.String(),
		ElapsedMs: elapsed,
	}

	// Prova a interpretare stdout come JSON
	out := bytes.TrimSpace(stdout.Bytes())
	if len(out) > 0 && (out[0] == '{' || out[0] == '[' || out[0] == '"') {
		// valid JSON? Se sì, metti come RawMessage
		resp.Stdout = json.RawMessage(out)
	} else if len(out) > 0 {
		resp.StdoutTxt = string(out)
	}

	code := http.StatusOK
	if !resp.OK {
		code = http.StatusBadGateway
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	if err := json.NewEncoder(w).Encode(resp); err != nil {
		log.Printf("encode response error: %v", err)
	}
}

// writeErr manda una risposta JSON di errore
func writeErr(w http.ResponseWriter, code int, err error) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(map[string]any{
		"ok":    false,
		"error": err.Error(),
	})
}

// exitCodeFromErr estrae l'exit code dall'errore di exec, se possibile
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
	// se il comando non è stato trovato o altro
	return 1
}

// getenvDefault ritorna valore di env o default
func getenvDefault(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}
