// api/main.go
// Portfolio AI — API Gateway (Go)
// Handles: auth, JWT validation, rate limiting, routing to agents and DB.
// Deployed on Render.com free tier.

package main

import (
	"crypto/sha256"
	"database/sql"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/gorilla/mux"
	pq "github.com/lib/pq"
	"github.com/rs/cors"
	"golang.org/x/crypto/bcrypt"
)

// ── Config ────────────────────────────────────────────────────────────────────

var (
	jwtSecret   = []byte(mustGetEnv("JWT_SECRET"))
	dbURL       = mustGetEnv("DATABASE_URL")
	advisorURL  = getEnvOr("ADVISOR_URL", "http://localhost:8001")
	environment = getEnvOr("ENVIRONMENT", "production")
	allowedOrigins = getEnvOr("ALLOWED_ORIGINS", "") // comma-separated custom origins
)

var db *sql.DB

// Input validation patterns
var (
	emailRegex  = regexp.MustCompile(`^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$`)
	uuidRegex   = regexp.MustCompile(`^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$`)
	tickerRegex = regexp.MustCompile(`^[A-Z0-9\-\.&_]{1,20}$`)
)

const maxRequestBodySize = 1 << 20 // 1 MB

func mustGetEnv(key string) string {
	v := os.Getenv(key)
	if v == "" {
		log.Fatalf("Required env var %s is not set", key)
	}
	return v
}

func getEnvOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

// ── JWT ───────────────────────────────────────────────────────────────────────

type Claims struct {
	UserID string `json:"user_id"`
	Email  string `json:"email"`
	jwt.RegisteredClaims
}

func generateToken(userID, email string) (string, error) {
	claims := Claims{
		UserID: userID,
		Email:  email,
		RegisteredClaims: jwt.RegisteredClaims{
			ExpiresAt: jwt.NewNumericDate(time.Now().Add(1 * time.Hour)),
			IssuedAt:  jwt.NewNumericDate(time.Now()),
		},
	}
	token := jwt.NewWithClaims(jwt.SigningMethodHS256, claims)
	return token.SignedString(jwtSecret)
}

func validateToken(tokenStr string) (*Claims, error) {
	token, err := jwt.ParseWithClaims(tokenStr, &Claims{}, func(t *jwt.Token) (interface{}, error) {
		if _, ok := t.Method.(*jwt.SigningMethodHMAC); !ok {
			return nil, jwt.ErrSignatureInvalid
		}
		return jwtSecret, nil
	})
	if err != nil {
		return nil, err
	}
	claims, ok := token.Claims.(*Claims)
	if !ok || !token.Valid {
		return nil, jwt.ErrTokenInvalidClaims
	}
	return claims, nil
}

// ── Middleware ────────────────────────────────────────────────────────────────

// securityHeaders adds standard security headers to every response
func securityHeaders(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("X-Frame-Options", "DENY")
		w.Header().Set("X-XSS-Protection", "1; mode=block")
		w.Header().Set("Referrer-Policy", "strict-origin-when-cross-origin")
		w.Header().Set("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
		if environment == "production" {
			w.Header().Set("Strict-Transport-Security", "max-age=63072000; includeSubDomains")
		}
		next.ServeHTTP(w, r)
	})
}

// bodySizeLimit prevents oversized request bodies
func bodySizeLimit(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Body != nil {
			r.Body = http.MaxBytesReader(w, r.Body, maxRequestBodySize)
		}
		next(w, r)
	}
}

func authMiddleware(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		authHeader := r.Header.Get("Authorization")
		if authHeader == "" || !strings.HasPrefix(authHeader, "Bearer ") {
			jsonError(w, "Missing or invalid Authorization header", http.StatusUnauthorized)
			return
		}
		tokenStr := strings.TrimPrefix(authHeader, "Bearer ")
		claims, err := validateToken(tokenStr)
		if err != nil {
			jsonError(w, "Invalid or expired token", http.StatusUnauthorized)
			return
		}
		r.Header.Set("X-User-ID", claims.UserID)
		r.Header.Set("X-User-Email", claims.Email)
		next(w, r)
	}
}

// Simple in-memory rate limiter per IP (resets on restart — fine for free tier)
var (
	rateLimiter   = make(map[string][]time.Time)
	rateLimiterMu sync.Mutex
)

func clientIP(r *http.Request) string {
	if forwarded := r.Header.Get("X-Forwarded-For"); forwarded != "" {
		// X-Forwarded-For may be a comma-separated list; take the first (original client)
		return strings.TrimSpace(strings.SplitN(forwarded, ",", 2)[0])
	}
	return r.RemoteAddr
}

func rateLimitMiddleware(maxReqs int, window time.Duration, next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		ip := clientIP(r)
		now := time.Now()
		cutoff := now.Add(-window)

		rateLimiterMu.Lock()
		filtered := rateLimiter[ip][:0]
		for _, t := range rateLimiter[ip] {
			if t.After(cutoff) {
				filtered = append(filtered, t)
			}
		}
		rateLimiter[ip] = filtered
		limited := len(rateLimiter[ip]) >= maxReqs
		if !limited {
			rateLimiter[ip] = append(rateLimiter[ip], now)
		}
		rateLimiterMu.Unlock()

		if limited {
			jsonError(w, "Rate limit exceeded", http.StatusTooManyRequests)
			return
		}
		next(w, r)
	}
}

// ── Response helpers ──────────────────────────────────────────────────────────

func jsonOK(w http.ResponseWriter, data interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(data)
}

func jsonError(w http.ResponseWriter, msg string, code int) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(map[string]string{"error": msg})
}

// ── Handlers ──────────────────────────────────────────────────────────────────

// POST /auth/register
func handleRegister(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Email    string `json:"email"`
		Password string `json:"password"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		jsonError(w, "Invalid request body", http.StatusBadRequest)
		return
	}

	req.Email = strings.TrimSpace(strings.ToLower(req.Email))
	if !emailRegex.MatchString(req.Email) || len(req.Email) > 254 {
		jsonError(w, "Invalid email address", http.StatusBadRequest)
		return
	}
	if len(req.Password) < 8 || len(req.Password) > 128 {
		jsonError(w, "Password must be 8-128 characters", http.StatusBadRequest)
		return
	}

	hash, err := bcrypt.GenerateFromPassword([]byte(req.Password), bcrypt.DefaultCost)
	if err != nil {
		jsonError(w, "Internal error", http.StatusInternalServerError)
		return
	}

	var userID string
	err = db.QueryRow(
		"INSERT INTO users (email, password_hash) VALUES ($1, $2) RETURNING id",
		req.Email, string(hash),
	).Scan(&userID)
	if err != nil {
		if pqErr, ok := err.(*pq.Error); ok && pqErr.Code == "23505" {
			jsonError(w, "Email already registered", http.StatusConflict)
			return
		}
		jsonError(w, "Registration failed", http.StatusInternalServerError)
		return
	}

	token, _ := generateToken(userID, req.Email)
	jsonOK(w, map[string]string{"token": token, "user_id": userID})
}

// POST /auth/login
func handleLogin(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Email    string `json:"email"`
		Password string `json:"password"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		jsonError(w, "Invalid request", http.StatusBadRequest)
		return
	}

	var userID, hash string
	err := db.QueryRow(
		"SELECT id, password_hash FROM users WHERE email = $1 AND is_active = TRUE",
		req.Email,
	).Scan(&userID, &hash)
	if err == sql.ErrNoRows {
		jsonError(w, "Invalid credentials", http.StatusUnauthorized)
		return
	}
	if err != nil {
		jsonError(w, "Internal error", http.StatusInternalServerError)
		return
	}

	if err := bcrypt.CompareHashAndPassword([]byte(hash), []byte(req.Password)); err != nil {
		jsonError(w, "Invalid credentials", http.StatusUnauthorized)
		return
	}

	db.Exec("UPDATE users SET last_login_at = NOW() WHERE id = $1", userID)
	db.Exec("INSERT INTO audit_log (user_id, action, ip_address) VALUES ($1, 'login', $2)", userID, r.RemoteAddr)

	token, _ := generateToken(userID, req.Email)
	jsonOK(w, map[string]string{"token": token, "user_id": userID})
}

// GET /portfolio/holdings
func handleGetHoldings(w http.ResponseWriter, r *http.Request) {
	userID := r.Header.Get("X-User-ID")
	rows, err := db.Query(`
		SELECT ticker, exchange, company_name, sector, quantity, avg_cost,
		       currency, last_price, last_updated_at,
		       (last_price - avg_cost) / NULLIF(avg_cost, 0) * 100 AS unrealized_pct,
		       (last_price - avg_cost) * quantity AS unrealized_pnl
		FROM holdings WHERE user_id = $1 ORDER BY sector, ticker`, userID)
	if err != nil {
		jsonError(w, "Failed to fetch holdings", http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	holdings := make([]map[string]interface{}, 0)
	for rows.Next() {
		var ticker, exchange, currency string
		var companyName, sector sql.NullString
		var quantity, avgCost float64
		var lastPrice, unrealizedPct, unrealizedPnl sql.NullFloat64
		var lastUpdated sql.NullTime

		if err := rows.Scan(&ticker, &exchange, &companyName, &sector, &quantity,
			&avgCost, &currency, &lastPrice, &lastUpdated,
			&unrealizedPct, &unrealizedPnl); err != nil {
			continue
		}
		holdings = append(holdings, map[string]interface{}{
			"ticker":         ticker,
			"exchange":       exchange,
			"company_name":   companyName.String,
			"sector":         sector.String,
			"quantity":       quantity,
			"avg_cost":       avgCost,
			"currency":       currency,
			"last_price":     lastPrice.Float64,
			"unrealized_pct": unrealizedPct.Float64,
			"unrealized_pnl": unrealizedPnl.Float64,
		})
	}
	jsonOK(w, holdings)
}

// GET /alerts
func handleGetAlerts(w http.ResponseWriter, r *http.Request) {
	userID := r.Header.Get("X-User-ID")
	limit := 50
	if ls := r.URL.Query().Get("limit"); ls != "" {
		if n, err := strconv.Atoi(ls); err == nil && n > 0 && n <= 200 {
			limit = n
		}
	}

	rows, err := db.Query(`
		SELECT id, agent_type, ticker, severity, title, body,
		       confidence_pct, is_read, created_at
		FROM alerts
		WHERE user_id = $1 AND is_dismissed = FALSE
		ORDER BY created_at DESC LIMIT $2`, userID, limit)
	if err != nil {
		jsonError(w, "Failed to fetch alerts", http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	alerts := make([]map[string]interface{}, 0)
	for rows.Next() {
		var id, agentType, severity, title, body string
		var ticker sql.NullString
		var confidencePct sql.NullInt64
		var isRead bool
		var createdAt time.Time

		if err := rows.Scan(&id, &agentType, &ticker, &severity, &title, &body,
			&confidencePct, &isRead, &createdAt); err != nil {
			continue
		}
		alerts = append(alerts, map[string]interface{}{
			"id":             id,
			"agent_type":     agentType,
			"ticker":         ticker.String,
			"severity":       severity,
			"title":          title,
			"body":           body,
			"confidence_pct": confidencePct.Int64,
			"is_read":        isRead,
			"created_at":     createdAt,
		})
	}
	jsonOK(w, alerts)
}

// PATCH /alerts/{id}/read
func handleMarkAlertRead(w http.ResponseWriter, r *http.Request) {
	userID := r.Header.Get("X-User-ID")
	alertID := mux.Vars(r)["id"]
	if !uuidRegex.MatchString(alertID) {
		jsonError(w, "Invalid alert ID", http.StatusBadRequest)
		return
	}
	_, err := db.Exec(
		"UPDATE alerts SET is_read = TRUE WHERE id = $1 AND user_id = $2",
		alertID, userID,
	)
	if err != nil {
		jsonError(w, "Failed to mark alert read", http.StatusInternalServerError)
		return
	}
	jsonOK(w, map[string]bool{"success": true})
}

// GET /chat/history?session_id=<uuid>&limit=50
// Returns the message history for a session directly from the DB.
func handleChatHistory(w http.ResponseWriter, r *http.Request) {
	userID    := r.Header.Get("X-User-ID")
	sessionID := r.URL.Query().Get("session_id")
	if sessionID != "" && !uuidRegex.MatchString(sessionID) {
		jsonError(w, "Invalid session ID format", http.StatusBadRequest)
		return
	}
	limit     := 50
	if l := r.URL.Query().Get("limit"); l != "" {
		if n, err := strconv.Atoi(l); err == nil && n > 0 && n <= 200 {
			limit = n
		}
	}

	rows, err := db.Query(`
		SELECT role, content, created_at
		FROM chat_messages
		WHERE session_id = $1 AND user_id = $2
		ORDER BY created_at ASC
		LIMIT $3
	`, sessionID, userID, limit)
	if err != nil {
		jsonError(w, "Failed to load history", http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	type Message struct {
		Role      string    `json:"role"`
		Content   string    `json:"content"`
		CreatedAt time.Time `json:"created_at"`
	}
	messages := []Message{}
	for rows.Next() {
		var m Message
		if err := rows.Scan(&m.Role, &m.Content, &m.CreatedAt); err == nil {
			messages = append(messages, m)
		}
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{"messages": messages})
}

// POST /chat — proxy to advisor agent
func handleChat(w http.ResponseWriter, r *http.Request) {
	userID := r.Header.Get("X-User-ID")
	var req map[string]interface{}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		jsonError(w, "Invalid request body", http.StatusBadRequest)
		return
	}
	req["user_id"] = userID // inject authenticated user ID — never trust client

	body, _ := json.Marshal(req)
	resp, err := http.Post(advisorURL+"/chat", "application/json", strings.NewReader(string(body)))
	if err != nil {
		jsonError(w, "Advisor service unavailable", http.StatusServiceUnavailable)
		return
	}
	defer resp.Body.Close()

	var result map[string]interface{}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil || result == nil {
		jsonError(w, "Advisor returned an unexpected response", http.StatusBadGateway)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(resp.StatusCode)
	json.NewEncoder(w).Encode(result)
}

// POST /admin/zerodha/sync
// Accepts a Zerodha request_token, exchanges it for an access token,
// fetches live holdings, and upserts them into the holdings table.
// See CURSOR_CONTEXT.md for the daily manual login workflow.
func handleZerodhaSync(w http.ResponseWriter, r *http.Request) {
	userID := r.Header.Get("X-User-ID")

	var req struct {
		RequestToken string `json:"request_token"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.RequestToken == "" {
		jsonError(w, "request_token is required", http.StatusBadRequest)
		return
	}

	apiKey := os.Getenv("ZERODHA_API_KEY")
	apiSecret := os.Getenv("ZERODHA_API_SECRET")
	if apiKey == "" || apiSecret == "" {
		jsonError(w, "Zerodha credentials not configured", http.StatusInternalServerError)
		return
	}

	// Step 1 — exchange request_token for access_token
	checksum := zerodhaChecksum(apiKey, req.RequestToken, apiSecret)
	tokenResp, err := http.PostForm("https://api.kite.trade/session/token", url.Values{
		"api_key":       {apiKey},
		"request_token": {req.RequestToken},
		"checksum":      {checksum},
	})
	if err != nil || tokenResp.StatusCode != 200 {
		jsonError(w, "Failed to exchange Zerodha request token", http.StatusBadGateway)
		return
	}
	defer tokenResp.Body.Close()

	var tokenData struct {
		Data struct {
			AccessToken string `json:"access_token"`
		} `json:"data"`
	}
	json.NewDecoder(tokenResp.Body).Decode(&tokenData)
	accessToken := tokenData.Data.AccessToken
	if accessToken == "" {
		jsonError(w, "Empty access token from Zerodha", http.StatusBadGateway)
		return
	}

	// Step 2 — fetch holdings
	holdingsReq, _ := http.NewRequest("GET", "https://api.kite.trade/portfolio/holdings", nil)
	holdingsReq.Header.Set("X-Kite-Version", "3")
	holdingsReq.Header.Set("Authorization", "token "+apiKey+":"+accessToken)

	client := &http.Client{Timeout: 15 * time.Second}
	holdingsResp, err := client.Do(holdingsReq)
	if err != nil || holdingsResp.StatusCode != 200 {
		jsonError(w, "Failed to fetch Zerodha holdings", http.StatusBadGateway)
		return
	}
	defer holdingsResp.Body.Close()

	var holdingsData struct {
		Data []struct {
			Tradingsymbol string  `json:"tradingsymbol"`
			Exchange      string  `json:"exchange"`
			Quantity      int     `json:"quantity"`
			AveragePrice  float64 `json:"average_price"`
			LastPrice     float64 `json:"last_price"`
		} `json:"data"`
	}
	rawBody, _ := io.ReadAll(holdingsResp.Body)
	json.Unmarshal(rawBody, &holdingsData)

	// Step 3 — upsert into holdings table
	upserted := 0
	for _, h := range holdingsData.Data {
		if h.Quantity <= 0 {
			continue
		}
		_, err := db.Exec(`
			INSERT INTO holdings
				(user_id, ticker, exchange, quantity, avg_cost, last_price, currency, last_updated_at)
			VALUES ($1,$2,$3,$4,$5,$6,'INR',NOW())
			ON CONFLICT (user_id, ticker, exchange) DO UPDATE SET
				quantity        = EXCLUDED.quantity,
				avg_cost        = EXCLUDED.avg_cost,
				last_price      = EXCLUDED.last_price,
				last_updated_at = NOW()`,
			userID, h.Tradingsymbol, h.Exchange,
			h.Quantity, h.AveragePrice, h.LastPrice,
		)
		if err == nil {
			upserted++
		}
	}

	db.Exec("INSERT INTO audit_log (user_id, action, resource_type) VALUES ($1, 'zerodha_sync', 'holdings')", userID)
	jsonOK(w, map[string]interface{}{"synced": upserted, "status": "ok"})
}

// zerodhaChecksum = SHA-256(api_key + request_token + api_secret)
func zerodhaChecksum(apiKey, requestToken, apiSecret string) string {
	h := sha256.Sum256([]byte(apiKey + requestToken + apiSecret))
	return fmt.Sprintf("%x", h)
}

// GET /portfolio/holdings/:ticker — single holding detail with alerts
func handleGetHoldingDetail(w http.ResponseWriter, r *http.Request) {
	userID := r.Header.Get("X-User-ID")
	ticker := strings.ToUpper(mux.Vars(r)["ticker"])
	if !tickerRegex.MatchString(ticker) {
		jsonError(w, "Invalid ticker symbol", http.StatusBadRequest)
		return
	}

	// Fetch holding
	var exchange, currency string
	var companyName, sector sql.NullString
	var quantity, avgCost float64
	var lastPrice, unrealizedPct, unrealizedPnl sql.NullFloat64
	var lastUpdated sql.NullTime

	err := db.QueryRow(`
		SELECT ticker, exchange, company_name, sector, quantity, avg_cost,
		       currency, last_price, last_updated_at,
		       (last_price - avg_cost) / NULLIF(avg_cost, 0) * 100 AS unrealized_pct,
		       (last_price - avg_cost) * quantity AS unrealized_pnl
		FROM holdings WHERE user_id = $1 AND UPPER(ticker) = $2
		LIMIT 1`, userID, ticker,
	).Scan(&ticker, &exchange, &companyName, &sector, &quantity,
		&avgCost, &currency, &lastPrice, &lastUpdated,
		&unrealizedPct, &unrealizedPnl)

	if err == sql.ErrNoRows {
		jsonError(w, "Holding not found", http.StatusNotFound)
		return
	}
	if err != nil {
		jsonError(w, "Failed to fetch holding", http.StatusInternalServerError)
		return
	}

	holding := map[string]interface{}{
		"ticker":         ticker,
		"exchange":       exchange,
		"company_name":   companyName.String,
		"sector":         sector.String,
		"quantity":       quantity,
		"avg_cost":       avgCost,
		"currency":       currency,
		"last_price":     lastPrice.Float64,
		"unrealized_pct": unrealizedPct.Float64,
		"unrealized_pnl": unrealizedPnl.Float64,
		"last_updated":   lastUpdated.Time,
	}

	// Fetch recent alerts for this ticker
	alertRows, err := db.Query(`
		SELECT id, agent_type, severity, title, body, confidence_pct, is_read, created_at
		FROM alerts
		WHERE user_id = $1 AND UPPER(ticker) = $2 AND is_dismissed = FALSE
		ORDER BY created_at DESC LIMIT 20`, userID, ticker)
	if err != nil {
		// Non-fatal — return holding without alerts
		jsonOK(w, map[string]interface{}{"holding": holding, "alerts": []interface{}{}, "total_invested": avgCost * quantity, "current_value": lastPrice.Float64 * quantity})
		return
	}
	defer alertRows.Close()

	tickerAlerts := make([]map[string]interface{}, 0)
	for alertRows.Next() {
		var id, agentType, severity, title, body string
		var confidencePct sql.NullInt64
		var isRead bool
		var createdAt time.Time

		if err := alertRows.Scan(&id, &agentType, &severity, &title, &body,
			&confidencePct, &isRead, &createdAt); err != nil {
			continue
		}
		tickerAlerts = append(tickerAlerts, map[string]interface{}{
			"id":             id,
			"agent_type":     agentType,
			"severity":       severity,
			"title":          title,
			"body":           body,
			"confidence_pct": confidencePct.Int64,
			"is_read":        isRead,
			"created_at":     createdAt,
		})
	}

	jsonOK(w, map[string]interface{}{
		"holding":        holding,
		"alerts":         tickerAlerts,
		"total_invested": avgCost * quantity,
		"current_value":  lastPrice.Float64 * quantity,
	})
}

// GET /alerts/ticker/:ticker — alerts for a specific ticker
func handleGetAlertsByTicker(w http.ResponseWriter, r *http.Request) {
	userID := r.Header.Get("X-User-ID")
	ticker := strings.ToUpper(mux.Vars(r)["ticker"])
	if !tickerRegex.MatchString(ticker) {
		jsonError(w, "Invalid ticker symbol", http.StatusBadRequest)
		return
	}

	rows, err := db.Query(`
		SELECT id, agent_type, severity, title, body,
		       confidence_pct, is_read, created_at
		FROM alerts
		WHERE user_id = $1 AND UPPER(ticker) = $2 AND is_dismissed = FALSE
		ORDER BY created_at DESC LIMIT 50`, userID, ticker)
	if err != nil {
		jsonError(w, "Failed to fetch alerts", http.StatusInternalServerError)
		return
	}
	defer rows.Close()

	alerts := make([]map[string]interface{}, 0)
	for rows.Next() {
		var id, agentType, severity, title, body string
		var confidencePct sql.NullInt64
		var isRead bool
		var createdAt time.Time

		if err := rows.Scan(&id, &agentType, &severity, &title, &body,
			&confidencePct, &isRead, &createdAt); err != nil {
			continue
		}
		alerts = append(alerts, map[string]interface{}{
			"id":             id,
			"agent_type":     agentType,
			"severity":       severity,
			"title":          title,
			"body":           body,
			"confidence_pct": confidencePct.Int64,
			"is_read":        isRead,
			"created_at":     createdAt,
		})
	}
	jsonOK(w, alerts)
}

// PATCH /alerts/{id}/dismiss
func handleDismissAlert(w http.ResponseWriter, r *http.Request) {
	userID := r.Header.Get("X-User-ID")
	alertID := mux.Vars(r)["id"]
	if !uuidRegex.MatchString(alertID) {
		jsonError(w, "Invalid alert ID", http.StatusBadRequest)
		return
	}
	_, err := db.Exec(
		"UPDATE alerts SET is_dismissed = TRUE WHERE id = $1 AND user_id = $2",
		alertID, userID,
	)
	if err != nil {
		jsonError(w, "Failed to dismiss alert", http.StatusInternalServerError)
		return
	}
	jsonOK(w, map[string]bool{"success": true})
}

// GET /portfolio/news/{ticker} — live news from NewsAPI for a specific stock
func handleGetNewsForTicker(w http.ResponseWriter, r *http.Request) {
	userID := r.Header.Get("X-User-ID")
	ticker := strings.ToUpper(mux.Vars(r)["ticker"])
	if !tickerRegex.MatchString(ticker) {
		jsonError(w, "Invalid ticker symbol", http.StatusBadRequest)
		return
	}

	newsAPIKey := os.Getenv("NEWS_API_KEY")
	if newsAPIKey == "" {
		jsonOK(w, map[string]interface{}{"articles": []interface{}{}, "message": "News API key not configured"})
		return
	}

	// Get company name from holdings to improve search
	var companyName sql.NullString
	db.QueryRow("SELECT company_name FROM holdings WHERE user_id = $1 AND UPPER(ticker) = $2", userID, ticker).Scan(&companyName)

	// Build query: prefer company name for better results, fall back to ticker
	query := ticker
	if companyName.Valid && companyName.String != "" {
		query = "\"" + companyName.String + "\" OR " + ticker
	}

	client := &http.Client{Timeout: 10 * time.Second}
	newsURL := "https://newsapi.org/v2/everything?q=" + url.QueryEscape(query) +
		"&apiKey=" + newsAPIKey +
		"&language=en&sortBy=publishedAt&pageSize=10"

	resp, err := client.Get(newsURL)
	if err != nil || resp.StatusCode != 200 {
		jsonOK(w, map[string]interface{}{"articles": []interface{}{}, "error": "Failed to fetch news"})
		return
	}
	defer resp.Body.Close()

	var newsResp struct {
		Articles []struct {
			Title       string `json:"title"`
			Description string `json:"description"`
			URL         string `json:"url"`
			Source      struct {
				Name string `json:"name"`
			} `json:"source"`
			PublishedAt string `json:"publishedAt"`
		} `json:"articles"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&newsResp); err != nil {
		jsonOK(w, map[string]interface{}{"articles": []interface{}{}})
		return
	}

	articles := make([]map[string]interface{}, 0, len(newsResp.Articles))
	for _, a := range newsResp.Articles {
		if a.Title == "" || a.Title == "[Removed]" {
			continue
		}
		articles = append(articles, map[string]interface{}{
			"title":        a.Title,
			"description":  a.Description,
			"url":          a.URL,
			"source":       a.Source.Name,
			"published_at": a.PublishedAt,
		})
	}
	jsonOK(w, map[string]interface{}{"articles": articles, "ticker": ticker})
}

// GET /portfolio/youtube/{ticker} — YouTube insights stored from channel monitoring
func handleGetYouTubeInsights(w http.ResponseWriter, r *http.Request) {
	userID := r.Header.Get("X-User-ID")
	ticker := strings.ToUpper(mux.Vars(r)["ticker"])
	if !tickerRegex.MatchString(ticker) {
		jsonError(w, "Invalid ticker symbol", http.StatusBadRequest)
		return
	}

	// Get channels this user is subscribed to
	channelRows, err := db.Query(
		"SELECT channel_id FROM youtube_channels WHERE user_id = $1 AND is_active = TRUE", userID)
	if err != nil {
		jsonOK(w, map[string]interface{}{"videos": []interface{}{}})
		return
	}
	defer channelRows.Close()

	channelIDs := make([]string, 0)
	for channelRows.Next() {
		var id string
		if channelRows.Scan(&id) == nil {
			channelIDs = append(channelIDs, id)
		}
	}

	if len(channelIDs) == 0 {
		jsonOK(w, map[string]interface{}{"videos": []interface{}{}, "message": "No YouTube channels configured"})
		return
	}

	// Find videos that mention this ticker
	rows, err := db.Query(`
		SELECT video_id, title, published_at, insights, channel_id
		FROM youtube_videos
		WHERE channel_id = ANY($1)
		  AND $2 = ANY(tickers_mentioned)
		  AND processed_at IS NOT NULL
		ORDER BY published_at DESC
		LIMIT 5`, pq.Array(channelIDs), ticker)
	if err != nil {
		jsonOK(w, map[string]interface{}{"videos": []interface{}{}})
		return
	}
	defer rows.Close()

	videos := make([]map[string]interface{}, 0)
	for rows.Next() {
		var videoID, title, channelID string
		var publishedAt time.Time
		var insightsJSON []byte
		if err := rows.Scan(&videoID, &title, &publishedAt, &insightsJSON, &channelID); err != nil {
			continue
		}
		var insights map[string]interface{}
		json.Unmarshal(insightsJSON, &insights)

		// Extract the insight for this specific ticker
		var tickerInsight map[string]interface{}
		if insightsList, ok := insights["insights"].([]interface{}); ok {
			for _, ins := range insightsList {
				if insMap, ok := ins.(map[string]interface{}); ok {
					if insMap["ticker"] == ticker {
						tickerInsight = insMap
						break
					}
				}
			}
		}

		videos = append(videos, map[string]interface{}{
			"video_id":     videoID,
			"title":        title,
			"published_at": publishedAt,
			"url":          "https://youtube.com/watch?v=" + videoID,
			"insight":      tickerInsight,
		})
	}
	jsonOK(w, map[string]interface{}{"videos": videos, "ticker": ticker})
}

// GET /health
func handleHealth(w http.ResponseWriter, r *http.Request) {
	jsonOK(w, map[string]string{"status": "ok", "service": "api-gateway"})
}

// ── Main ──────────────────────────────────────────────────────────────────────

func main() {
	var err error
	db, err = sql.Open("postgres", dbURL)
	if err != nil {
		log.Fatalf("Failed to connect to database: %v", err)
	}
	db.SetMaxOpenConns(10)
	db.SetMaxIdleConns(5)
	db.SetConnMaxLifetime(5 * time.Minute)

	if err := db.Ping(); err != nil {
		log.Fatalf("Database ping failed: %v", err)
	}
	log.Println("Database connected")

	r := mux.NewRouter()

	// Public routes (rate limited + body size limited)
	r.HandleFunc("/health", handleHealth).Methods("GET")
	r.HandleFunc("/auth/register", rateLimitMiddleware(5, time.Minute, bodySizeLimit(handleRegister))).Methods("POST")
	r.HandleFunc("/auth/login", rateLimitMiddleware(10, time.Minute, bodySizeLimit(handleLogin))).Methods("POST")

	// Protected routes (JWT required + body size limited)
	r.HandleFunc("/portfolio/holdings", authMiddleware(handleGetHoldings)).Methods("GET")
	r.HandleFunc("/portfolio/holdings/{ticker}", authMiddleware(handleGetHoldingDetail)).Methods("GET")
	r.HandleFunc("/portfolio/news/{ticker}", authMiddleware(handleGetNewsForTicker)).Methods("GET")
	r.HandleFunc("/portfolio/youtube/{ticker}", authMiddleware(handleGetYouTubeInsights)).Methods("GET")
	r.HandleFunc("/alerts", authMiddleware(handleGetAlerts)).Methods("GET")
	r.HandleFunc("/alerts/ticker/{ticker}", authMiddleware(handleGetAlertsByTicker)).Methods("GET")
	r.HandleFunc("/alerts/{id}/read", authMiddleware(handleMarkAlertRead)).Methods("PATCH")
	r.HandleFunc("/alerts/{id}/dismiss", authMiddleware(handleDismissAlert)).Methods("PATCH")
	r.HandleFunc("/chat", authMiddleware(rateLimitMiddleware(20, time.Minute, bodySizeLimit(handleChat)))).Methods("POST")
	r.HandleFunc("/chat/history", authMiddleware(handleChatHistory)).Methods("GET")
	r.HandleFunc("/admin/zerodha/sync", authMiddleware(bodySizeLimit(handleZerodhaSync))).Methods("POST")

	// CORS — allow Vercel previews + localhost + custom origins
	c := cors.New(cors.Options{
		AllowOriginFunc: func(origin string) bool {
			if strings.HasSuffix(origin, ".vercel.app") ||
				strings.HasPrefix(origin, "http://localhost") {
				return true
			}
			// Allow custom origins from env (comma-separated)
			if allowedOrigins != "" {
				for _, o := range strings.Split(allowedOrigins, ",") {
					if strings.TrimSpace(o) == origin {
						return true
					}
				}
			}
			return false
		},
		AllowedMethods:   []string{"GET", "POST", "PATCH", "DELETE"},
		AllowedHeaders:   []string{"Authorization", "Content-Type"},
		AllowCredentials: true,
		MaxAge:           300,
	})

	port := getEnvOr("PORT", "8080")
	log.Printf("API gateway starting on port %s", port)
	log.Fatal(http.ListenAndServe(":"+port, securityHeaders(c.Handler(r))))
}
