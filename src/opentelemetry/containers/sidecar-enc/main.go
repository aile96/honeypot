package main

import (
	"bytes"
	"context"
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"

	flagd "github.com/open-feature/go-sdk-contrib/providers/flagd/pkg"
	of "github.com/open-feature/go-sdk/openfeature"
	"golang.org/x/net/http2"
	"golang.org/x/net/http2/h2c"
)

const (
	headerAuth      = "X-Auth-Token"
	headerEncrypted = "X-Encrypted"
	headerOrigCT    = "X-Orig-Content-Type"

	defaultFlagKey   = "cryptoWord"
	defaultHeaderKey = headerAuth
)

// Env knobs:
//   CRYPTO_USE_FLAGD=true|false  -> switch between flagd and static default
//   CRYPTO_DEFAULT_WORD=<string> -> the static word and the OpenFeature default

type encPayload struct {
	Nonce      string `json:"nonce"`
	Ciphertext string `json:"ciphertext"`
}

type proxy struct {
	mode        string // "egress" | "ingress"
	listenAddr  string
	upstreamURL *url.URL

	flagKey     string
	useFlagd    bool
	defaultWord string
	headerKey   string

	ofClient *of.Client

	// cached last non-empty word + timestamp (best-effort)
	lastWord string
	lastAt   time.Time
}

func mustEnv(key, def string) string {
	if v := strings.TrimSpace(os.Getenv(key)); v != "" {
		return v
	}
	return def
}

func getenvInt(key string, def int) int {
	if v := strings.TrimSpace(os.Getenv(key)); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return def
}

func getenvBool(key string, def bool) bool {
	v := strings.ToLower(strings.TrimSpace(os.Getenv(key)))
	if v == "" {
		return def
	}
	return v == "1" || v == "true" || v == "yes" || v == "on"
}

func deriveKey(word string) ([]byte, error) {
	if word == "" {
		return nil, fmt.Errorf("empty word")
	}
	sum := sha256.Sum256([]byte(word))
	return sum[:], nil
}

func encrypt(word string, plaintext []byte) ([]byte, error) {
	k, err := deriveKey(word)
	if err != nil {
		return nil, err
	}
	block, err := aes.NewCipher(k)
	if err != nil {
		return nil, err
	}
	aead, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	nonce := make([]byte, aead.NonceSize())
	if _, err = io.ReadFull(rand.Reader, nonce); err != nil {
		return nil, err
	}
	ct := aead.Seal(nil, nonce, plaintext, nil)
	msg := encPayload{
		Nonce:      base64.StdEncoding.EncodeToString(nonce),
		Ciphertext: base64.StdEncoding.EncodeToString(ct),
	}
	return json.Marshal(msg)
}

func decrypt(word string, payload []byte) ([]byte, error) {
	k, err := deriveKey(word)
	if err != nil {
		return nil, err
	}
	var msg encPayload
	if err := json.Unmarshal(payload, &msg); err != nil {
		return nil, fmt.Errorf("invalid encrypted payload: %w", err)
	}
	nonce, err := base64.StdEncoding.DecodeString(msg.Nonce)
	if err != nil {
		return nil, fmt.Errorf("invalid nonce: %w", err)
	}
	ct, err := base64.StdEncoding.DecodeString(msg.Ciphertext)
	if err != nil {
		return nil, fmt.Errorf("invalid ciphertext: %w", err)
	}
	block, err := aes.NewCipher(k)
	if err != nil {
		return nil, err
	}
	aead, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	pt, err := aead.Open(nil, nonce, ct, nil)
	if err != nil {
		return nil, fmt.Errorf("decryption failed: %w", err)
	}
	return pt, nil
}

func newProxy() (*proxy, error) {
	mode := mustEnv("MODE", "egress") // egress | ingress
	listen := mustEnv("LISTEN_ADDR", ":18080")
	up := mustEnv("UPSTREAM_URL", "")
	if up == "" {
		return nil, fmt.Errorf("UPSTREAM_URL required")
	}
	if !strings.Contains(up, "://") {
		up = "http://" + up
	}
	u, err := url.Parse(up)
	if err != nil {
		return nil, fmt.Errorf("invalid UPSTREAM_URL: %w", err)
	}

	// Decide source for the crypto word
	useFlagd := getenvBool("CRYPTO_USE_FLAGD", true)
	defWord := mustEnv("CRYPTO_DEFAULT_WORD", "")

	var client *of.Client
	if useFlagd {
		// flagd provider (gRPC 8013 by default)
		flagdHost := mustEnv("FLAGD_HOST", "flagd")
		flagdPort := uint16(getenvInt("FLAGD_PORT", 8013))
		provider, err := flagd.NewProvider(flagd.WithHost(flagdHost), flagd.WithPort(flagdPort))
		if err != nil {
			return nil, fmt.Errorf("flagd provider: %w", err)
		}

		// exponential retry to initialize OpenFeature (flagd may not be ready yet)
		var lastErr error
		for i, d := range []time.Duration{0, time.Second, 2 * time.Second, 4 * time.Second, 8 * time.Second, 16 * time.Second, 32 * time.Second} {
			if d > 0 {
				time.Sleep(d)
			}
			if err := of.SetProviderAndWait(provider); err != nil {
				lastErr = err
				log.Printf("[crypto-proxy] openfeature provider not ready (attempt %d): %v", i+1, err)
				continue
			}
			lastErr = nil
			break
		}
		if lastErr != nil {
			return nil, fmt.Errorf("openfeature set provider (after retries): %w", lastErr)
		}
		client = of.NewClient("crypto-proxy")
	}

	return &proxy{
		mode:        mode,
		listenAddr:  listen,
		upstreamURL: u,

		flagKey:     mustEnv("FLAG_KEY", defaultFlagKey),
		useFlagd:    useFlagd,
		defaultWord: defWord,
		headerKey:   mustEnv("HEADER_NAME", defaultHeaderKey),

		ofClient: client,
	}, nil
}

func (p *proxy) currentWord(ctx context.Context) string {
	// Static mode: always use configured default
	if !p.useFlagd {
		if strings.TrimSpace(p.defaultWord) != "" {
			p.lastWord = p.defaultWord
			p.lastAt = time.Now()
		}
		return p.defaultWord
	}

	// Flagd mode: ask provider; default to configured static word
	val, err := p.ofClient.StringValue(ctx, p.flagKey, p.defaultWord, of.EvaluationContext{})
	if err != nil {
		// best-effort fallback to the last non-empty value
		return p.lastWord
	}
	if strings.TrimSpace(val) != "" {
		p.lastWord = val
		p.lastAt = time.Now()
	}
	return val
}

func readAllAndClose(rc io.ReadCloser) ([]byte, error) {
	if rc == nil {
		return []byte{}, nil
	}
	defer rc.Close()
	return io.ReadAll(rc)
}

func isGRPCContentType(ct string) bool {
	return strings.HasPrefix(strings.ToLower(ct), "application/grpc")
}

type grpcFrameTransform func(payload []byte) ([]byte, error)

type grpcFrameTransformReadCloser struct {
	src       io.ReadCloser
	transform grpcFrameTransform
	buf       bytes.Buffer
	done      bool
}

func newGRPCFrameTransformReadCloser(src io.ReadCloser, transform grpcFrameTransform) io.ReadCloser {
	return &grpcFrameTransformReadCloser{
		src:       src,
		transform: transform,
	}
}

func (g *grpcFrameTransformReadCloser) Read(p []byte) (int, error) {
	for g.buf.Len() == 0 {
		if g.done {
			return 0, io.EOF
		}

		header := make([]byte, 5)
		if _, err := io.ReadFull(g.src, header); err != nil {
			if err == io.EOF {
				g.done = true
				return 0, io.EOF
			}
			if err == io.ErrUnexpectedEOF {
				return 0, fmt.Errorf("invalid gRPC frame header: %w", err)
			}
			return 0, err
		}

		frameLen := binary.BigEndian.Uint32(header[1:])
		framePayload := make([]byte, frameLen)
		if _, err := io.ReadFull(g.src, framePayload); err != nil {
			return 0, fmt.Errorf("invalid gRPC frame payload: %w", err)
		}

		transformedPayload, err := g.transform(framePayload)
		if err != nil {
			return 0, err
		}
		if uint64(len(transformedPayload)) > uint64(^uint32(0)) {
			return 0, fmt.Errorf("gRPC frame too large after transform: %d", len(transformedPayload))
		}

		binary.BigEndian.PutUint32(header[1:], uint32(len(transformedPayload)))
		g.buf.Write(header)
		g.buf.Write(transformedPayload)
	}

	return g.buf.Read(p)
}

func (g *grpcFrameTransformReadCloser) Close() error {
	return g.src.Close()
}

func (p *proxy) buildReverseProxy() *httputil.ReverseProxy {
	rp := httputil.NewSingleHostReverseProxy(p.upstreamURL)

	// --- Enable HTTP/2 (h2c) towards the upstream: required for cleartext gRPC apps
	rp.Transport = &http2.Transport{
		AllowHTTP: true,
		DialTLS: func(network, addr string, _ *tls.Config) (net.Conn, error) {
			// HTTP/2 cleartext (h2c)
			return net.Dial(network, addr)
		},
	}

	// Director: prepare the request for the upstream
	rp.Director = func(r *http.Request) {
		// Base URL/host rewrite
		r.URL.Scheme = p.upstreamURL.Scheme
		r.URL.Host = p.upstreamURL.Host

		// Avoid upstream compression when we manipulate bodies
		r.Header.Del("Accept-Encoding")

		ctx := r.Context()
		word := p.currentWord(ctx)
		transparent := strings.TrimSpace(word) == ""

		// gRPC detection
		isGRPC := isGRPCContentType(r.Header.Get("Content-Type"))

		if p.mode == "egress" {
			if transparent {
				// full pass-through
				return
			}
			// Always add token when the word is set (also for gRPC)
			r.Header.Set(p.headerKey, word)

			// For body-carrying methods, encrypt payloads (including gRPC frames)
			if r.Body != nil && (r.Method == http.MethodPost || r.Method == http.MethodPut || r.Method == http.MethodPatch) {
				if isGRPC {
					r.Header.Set(headerEncrypted, "1")
					r.Body = newGRPCFrameTransformReadCloser(r.Body, func(payload []byte) ([]byte, error) {
						return encrypt(word, payload)
					})
					r.ContentLength = -1
					r.Header.Del("Content-Length")
					return
				}

				origCT := r.Header.Get("Content-Type")
				body, _ := readAllAndClose(r.Body)
				enc, err := encrypt(word, body)
				if err != nil {
					// mark error: will be handled by ErrorHandler/ModifyResponse
					r.Header.Set("X-Crypto-BadEncrypt", "1")
					return
				}
				r.Header.Set(headerOrigCT, origCT)
				r.Header.Set(headerEncrypted, "1")
				r.Header.Set("Content-Type", "application/json")
				r.Body = io.NopCloser(bytes.NewReader(enc))
				r.ContentLength = int64(len(enc))
				r.Header.Del("Content-Length")
			}
			return
		}

		// INGRESS: verify token if word is active; decrypt ONLY if marked X-Encrypted
		if transparent {
			// defensive cleanup
			r.Header.Del(headerEncrypted)
			r.Header.Del(p.headerKey)
			r.Header.Del(headerOrigCT)
			return
		}

		// verify token (also for gRPC)
		if r.Header.Get(p.headerKey) != word {
			// signal 401 without calling upstream
			r.Header.Set("X-Crypto-Unauthorized", "1")
			return
		}

		// If the request arrives encrypted, decrypt
		if r.Header.Get(headerEncrypted) == "1" && r.Body != nil {
			if isGRPC {
				r.Body = newGRPCFrameTransformReadCloser(r.Body, func(payload []byte) ([]byte, error) {
					return decrypt(word, payload)
				})
				r.ContentLength = -1
				r.Header.Del("Content-Length")
				// cleanup
				r.Header.Del(headerEncrypted)
				r.Header.Del(headerOrigCT)
				r.Header.Del(p.headerKey)
				return
			}

			origCT := r.Header.Get(headerOrigCT)
			body, _ := readAllAndClose(r.Body)
			pt, err := decrypt(word, body)
			if err != nil {
				r.Header.Set("X-Crypto-BadDecrypt", "1")
				return
			}
			// restore CT and body
			if origCT != "" {
				r.Header.Set("Content-Type", origCT)
			} else {
				r.Header.Del("Content-Type")
			}
			r.Body = io.NopCloser(bytes.NewReader(pt))
			r.ContentLength = int64(len(pt))
			r.Header.Del("Content-Length")
			// cleanup
			r.Header.Del(headerEncrypted)
			r.Header.Del(headerOrigCT)
			r.Header.Del(p.headerKey)
		} else {
			// not encrypted: remove markers that are not needed by the app
			r.Header.Del(headerEncrypted)
			r.Header.Del(headerOrigCT)
			r.Header.Del(p.headerKey)
		}
	}

	// Manipulate the response from the upstream
	rp.ModifyResponse = func(resp *http.Response) error {
		req := resp.Request
		ctx := req.Context()
		word := p.currentWord(ctx)
		transparent := strings.TrimSpace(word) == ""

		// handle error signals set by the Director
		if req.Header.Get("X-Crypto-Unauthorized") == "1" {
			resp.StatusCode = http.StatusUnauthorized
			resp.Header = make(http.Header)
			resp.Body = io.NopCloser(bytes.NewBufferString("unauthorized"))
			resp.ContentLength = int64(len("unauthorized"))
			return nil
		}
		if req.Header.Get("X-Crypto-BadDecrypt") == "1" {
			resp.StatusCode = http.StatusBadRequest
			resp.Header = make(http.Header)
			resp.Body = io.NopCloser(bytes.NewBufferString("bad encrypted payload"))
			resp.ContentLength = int64(len("bad encrypted payload"))
			return nil
		}
		if req.Header.Get("X-Crypto-BadEncrypt") == "1" {
			resp.StatusCode = http.StatusBadGateway
			resp.Header = make(http.Header)
			resp.Body = io.NopCloser(bytes.NewBufferString("proxy encryption error"))
			resp.ContentLength = int64(len("proxy encryption error"))
			return nil
		}

		// gRPC detection from the request (more reliable)
		isGRPC := isGRPCContentType(req.Header.Get("Content-Type"))

		if p.mode == "egress" {
			// decrypt encrypted responses coming from the remote ingress proxy
			if transparent {
				return nil
			}
			if resp.Header.Get(headerEncrypted) == "1" {
				if isGRPC && resp.Body != nil {
					resp.Body = newGRPCFrameTransformReadCloser(resp.Body, func(payload []byte) ([]byte, error) {
						return decrypt(word, payload)
					})
					resp.ContentLength = -1
					resp.Header.Del("Content-Length")
					resp.Header.Del(headerEncrypted)
					resp.Header.Del(headerOrigCT)
					return nil
				}

				origCT := resp.Header.Get(headerOrigCT)
				body, _ := readAllAndClose(resp.Body)
				pt, err := decrypt(word, body)
				if err != nil {
					return err
				}
				resp.Body = io.NopCloser(bytes.NewReader(pt))
				resp.ContentLength = int64(len(pt))
				resp.Header.Del("Content-Length")
				resp.Header.Del(headerEncrypted)
				if origCT != "" {
					resp.Header.Set("Content-Type", origCT)
				} else {
					resp.Header.Del("Content-Type")
				}
				resp.Header.Del(headerOrigCT)
			}
			return nil
		}

		// INGRESS: encrypt the response towards the caller when word is active
		if transparent {
			return nil
		}
		if isGRPC {
			if resp.Body != nil {
				resp.Header.Set(headerEncrypted, "1")
				resp.Body = newGRPCFrameTransformReadCloser(resp.Body, func(payload []byte) ([]byte, error) {
					return encrypt(word, payload)
				})
				resp.ContentLength = -1
				resp.Header.Del("Content-Length")
			}
			return nil
		}

		body, _ := readAllAndClose(resp.Body)
		enc, err := encrypt(word, body)
		if err != nil {
			return err
		}
		origCT := resp.Header.Get("Content-Type")
		resp.Header.Set(headerOrigCT, origCT)
		resp.Header.Set(headerEncrypted, "1")
		resp.Header.Set("Content-Type", "application/json")
		resp.Body = io.NopCloser(bytes.NewReader(enc))
		resp.ContentLength = int64(len(enc))
		resp.Header.Del("Content-Length")
		return nil
	}

	// ErrorHandler for cases where the Director set markers but there's no ModifyResponse
	rp.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
		log.Printf("[crypto-proxy] proxy error -> upstream %s: %v", p.upstreamURL.String(), err)
		if r != nil && r.Header.Get("X-Crypto-Unauthorized") == "1" {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		if r != nil && r.Header.Get("X-Crypto-BadDecrypt") == "1" {
			http.Error(w, "bad encrypted payload", http.StatusBadRequest)
			return
		}
		if r != nil && r.Header.Get("X-Crypto-BadEncrypt") == "1" {
			http.Error(w, "proxy encryption error", http.StatusBadGateway)
			return
		}
		http.Error(w, "proxy error", http.StatusBadGateway)
	}

	return rp
}

func (p *proxy) serve() error {
	rp := p.buildReverseProxy()

	// Enable HTTP/2 cleartext (h2c) on the server side to support gRPC clients without TLS
	h2cHandler := h2c.NewHandler(rp, &http2.Server{})

	server := &http.Server{
		Addr:              p.listenAddr,
		Handler:           h2cHandler,
		ReadHeaderTimeout: 10 * time.Second,
	}
	log.Printf(
		"[crypto-proxy] mode=%s listen=%s upstream=%s flagKey=%s useFlagd=%t defaultWordSet=%t",
		p.mode, p.listenAddr, p.upstreamURL.String(), p.flagKey,
		p.useFlagd, strings.TrimSpace(p.defaultWord) != "",
	)
	return server.ListenAndServe()
}

func main() {
	px, err := newProxy()
	if err != nil {
		log.Fatal(err)
	}
	if err := px.serve(); err != nil {
		log.Fatal(err)
	}
}
