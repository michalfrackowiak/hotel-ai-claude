# Roadmap — Hotel-AI-Claude (Alpha)

**Current State (as of 2026-11-24):**
- Docker container running on port 8888 ✅
- All 320 tests passing ✅
- Core features complete: Voice calls, Chat, Booking engine, Dashboard, Guest accounts, Google SSO, OpenLine integration

---

## ✅ COMPLETED (Current Release)

| Task | Description | Commit |
|------|-------------|--------|
| 1 | Password change flow with session retention & redirect | d004380 |
| 2 | OpenLine settings configuration (BASE_URL, API_KEY, WEBHOOK_SECRET) | 7b5abb3 |
| 3 | Import cleanup in PasswordChangeView | 0468fb7 |
| 4 | Password change flow tests (session + redirect) | 9f1c302 |
| 5 | OpenLine webhook endpoint at `/integrations/openline/` | 5c4b90d |
| 6 | Booking routing fix (`/booking/` vs `/bookings/booking/`) | f79415b |
| 7 | Auto-assign room when `room_number` omitted | d004380 |
| 8 | Booking.com-style occupancy popover on dashboard | 7b5abb3 |
| 9 | Voice → Booking integration: history API + template display | d004380 |

---

## 🎯 PRIORITY 1: Production Readiness

### 1.1 Docker & Deployment Hardening
- [ ] **Multi-stage Dockerfile** — reduce image size (currently ~1.2GB)
- [ ] **Health check endpoint** — add `/health/` liveness/readiness probes for Kubernetes
- [ ] **Non-root user** — run gunicorn as non-root inside container
- [ ] **Static file serving** — whitenoise config review; consider CDN for production
- [ ] **Secrets management** — document 1Password / Doppler / AWS Secrets Manager integration
- [ ] **Docker Compose override** — `docker-compose.prod.yml` for production (no volume mounts, proper env)

### 1.2 Database & Migrations
- [ ] **Migration squashing** — squash initial migrations before 1.0 release
- [ ] **PostgreSQL tuning** — `work_mem`, `maintenance_work_mem`, connection pooling (PgBouncer)
- [ ] **Backup strategy** — document pg_dump / point-in-time recovery procedure
- [ ] **Migration safety** — add `django-migration-linter` to CI

### 1.3 Observability
- [ ] **Structured logging** — JSON logs with correlation IDs (request_id)
- [ ] **Metrics** — Prometheus `/metrics` endpoint (django-prometheus)
- [ ] **Tracing** — OpenTelemetry integration for request tracing
- [ ] **Error tracking** — Sentry / Glitchtip integration
- [ ] **Alerting** — basic alerts: 5xx rate, queue depth, DB connections

---

## 🎯 PRIORITY 2: Voice & AI Quality

### 2.1 Voice Pipeline Improvements
- [ ] **WebRTC media streaming** — replace HTTP polling with Twilio Media Streams / WebSocket for sub-200ms latency
- [ ] **VAD (Voice Activity Detection)** — client-side Silero VAD to reduce STT calls on silence
- [ ] **Barge-in / interruption handling** — stop TTS playback when user starts speaking
- [ ] **Streaming STT** — incremental transcription during speech (Whisper streaming / Deepgram)
- [ ] **Streaming TTS** — play audio chunks as they arrive (OpenAI / ElevenLabs streaming)

### 2.2 AI Agent Enhancements
- [ ] **Function calling native** — migrate from JSON-parsing tool loop to native function calling (OpenAI/Anthropic/Ollama)
- [ ] **Conversation summarization** — compact long histories for token efficiency
- [ ] **Memory / context injection** — inject guest profile, preferences, past stays into system prompt
- [ ] **Multi-language detection** — auto-detect language from first utterance
- [ ] **Fallback chains** — provider failover (OpenAI → Anthropic → Ollama) with circuit breaker

### 2.3 Speech Provider Hardening
- [ ] **Whisper local fallback** — faster-whisper / whisper.cpp for offline STT
- [ ] **Piper / Coqui TTS** — local neural TTS fallback
- [ ] **Audio format normalization** — handle opus, webm, wav, mp3 uniformly
- [ ] **STT confidence thresholds** — reject low-confidence transcripts

---

## 🎯 PRIORITY 3: Booking Engine & Guest Experience

### 3.1 Booking Features
- [ ] **Rate plans / packages** — breakfast included, non-refundable, corporate rates
- [ ] **Promo codes / discounts** — percentage, fixed amount, stay X pay Y
- [ ] **Group bookings** — multiple rooms under one reservation
- [ ] **Waitlist** — notify when room type becomes available
- [ ] **Partial stay / split booking** — different room types across stay
- [ ] **Early check-in / late check-out** — surcharge handling

### 3.2 Guest Portal
- [ ] **Booking modification** — change dates, room type, guests (with availability check)
- [ ] **Cancellation policy display** — show refund amount before confirming cancel
- [ ] **Pre-arrival form** — ID scan, special requests, estimated arrival time
- [ ] **Digital key / mobile key** — placeholder for future PMS integration
- [ ] **Invoice / receipt download** — PDF generation
- [ ] **Loyalty points** — simple points per night stayed

### 3.3 Communications
- [ ] **Email templates** — booking confirmation, reminder, check-in instructions, post-stay survey
- [ ] **SMS / WhatsApp** — Twilio integration for critical notifications
- [ ] **In-app notifications** — bell icon with toast messages
- [ ] **Template localization** — Polish + English for all comms

---

## 🎯 PRIORITY 4: Dashboard & Staff Tools

### 4.1 Dashboard Enhancements
- [ ] **Drag-and-drop room assignment** — reassign bookings between rooms visually
- [ ] **Housekeeping status** — dirty / cleaning / inspected / ready per room
- [ ] **Maintenance mode** — mark rooms OOO with reason
- [ ] **Revenue metrics** — ADR, RevPAR, occupancy % charts (Chart.js / ApexCharts)
- [ ] **Shift handoff notes** — per-shift log for front desk

### 4.2 Reporting
- [ ] **Daily flash report** — arrivals, departures, in-house, revenue
- [ ] **Monthly P&L** — room revenue, extras, commissions
- [ ] **Guest history export** — CSV for marketing / CRM
- [ ] **Tax / VAT reports** — per jurisdiction

### 4.3 Staff Features
- [ ] **Role-based access** — admin / manager / receptionist / housekeeping
- [ ] **Audit log** — immutable log of all booking changes (who, when, what)
- [ ] **Impersonation** — admin "view as guest" for support
- [ ] **Bulk operations** — multi-select bookings → bulk check-in, message, export

---

## 🎯 PRIORITY 5: Integrations & Ecosystem

### 5.1 Channel Manager / PMS
- [ ] **OpenLine webhook hardening** — idempotency keys, retry with exponential backoff
- [ ] **iCal export/import** — sync with Airbnb, Booking.com, Expedia
- [ ] **Rate push** — push rates/availability to channel manager
- [ ] **Reservation pull** — ingest bookings from channels

### 5.2 Payments
- [ ] **Stripe / Adyen integration** — pre-auth, capture, refund
- [ ] **3D Secure** — SCA compliance for EU
- [ ] **Virtual cards** — for OTA payments
- [ ] **PCI DSS scope reduction** — tokenize, never store PAN

### 5.3 Third-Party Services
- [ ] **Review aggregation** — Google, TripAdvisor, Booking.com reviews in dashboard
- [ ] **Weather widget** — local forecast for guest info
- [ ] **Transport integration** — taxi/transfer booking API
- [ ] **Upsell marketplace** — spa, dining, tours at check-in

---

## 🎯 PRIORITY 6: Testing & Quality

### 6.1 Test Coverage
- [ ] **E2E browser tests** — Playwright for critical flows (booking, voice, password change)
- [ ] **Contract tests** — API schema validation (schemathesis / dredd)
- [ ] **Load testing** — Locust/k6 scripts for 100 concurrent users
- [ ] **Mutation testing** — mutmut to verify test quality

### 6.2 CI/CD Pipeline
- [ ] **GitHub Actions / GitLab CI** — lint, type-check, test, build, deploy
- [ ] **Pre-commit hooks** — ruff, black, mypy, djlint
- [ ] **Dependency scanning** — dependabot / renovate + trivy for vulnerabilities
- [ ] **Release automation** — semantic-release, changelog generation

---

## 🎯 PRIORITY 7: Architecture & Technical Debt

### 7.1 Code Organization
- [ ] **Domain-driven structure** — `hotel_ai/{bookings,guests,inventory,pricing}/` with clear boundaries
- [ ] **Service layer extraction** — move business logic out of views into `services.py`
- [ ] **Event-driven architecture** — domain events (BookingCreated, GuestCheckedIn) for decoupling
- [ ] **CQRS for reads** — materialized views for dashboard queries

### 7.2 Performance
- [ ] **N+1 query audit** — `select_related`/`prefetch_related` everywhere
- [ ] **Caching strategy** — Redis for availability, room grid, agent settings
- [ ] **Database indexes** — audit slow queries, add composite indexes
- [ ] **Async views** — migrate to ASGI/Django Channels for WebSocket support

### 7.3 Security
- [ ] **CSP headers** — Content-Security-Policy for XSS mitigation
- [ ] **Rate limiting** — django-ratelimit on auth endpoints, voice/chat APIs
- [ ] **Input sanitization** — bleach on all user-generated content
- [ ] **Dependency audit** — `pip-audit` in CI, pin transitive deps

---

## 🎯 PRIORITY 8: Internationalization & Accessibility

### 8.1 i18n
- [ ] **Django translation strings** — `{% trans %}` / `gettext` for all UI text
- [ ] **Locale middleware** — `Accept-Language` header + URL prefix (`/en/`, `/pl/`)
- [ ] **Date/number formatting** — locale-aware templates
- [ ] **RTL support** — foundation for Arabic/Hebrew

### 8.2 Accessibility (WCAG 2.1 AA)
- [ ] **Semantic HTML** — proper headings, landmarks, ARIA labels
- [ ] **Keyboard navigation** — focus order, skip links, focus visible
- [ ] **Color contrast** — verify all themes meet 4.5:1
- [ ] **Screen reader testing** — NVDA/VoiceOver for voice & chat UIs
- [ ] **Live regions** — announce transcript updates, status changes

---

## 📋 BACKLOG / NICE-TO-HAVE

| Idea | Effort | Impact |
|------|--------|--------|
| Kiosk mode for lobby tablet | M | M |
| Voice call recording (opt-in) | L | M |
| AI-generated stay summary email | M | L |
| Predictive pricing (ML) | XL | H |
| Guest chat translation (real-time) | L | M |
| Apple/Google Wallet pass for booking | M | L |
| Voice biometrics for returning guests | XL | M |
| AR room preview | XL | L |
| Multi-property support | XL | H |
| Franchise/brand theming | L | M |

---

## 🗓️ SUGGESTED SPRINT SEQUENCE

| Sprint | Focus | Deliverable |
|--------|-------|-------------|
| 1 | Docker hardening + health checks | Production-ready container |
| 2 | Voice WebRTC + VAD | Sub-500ms latency voice |
| 3 | Booking modifications + emails | Guest self-service |
| 4 | Dashboard drag-drop + housekeeping | Staff efficiency |
| 5 | Channel manager iCal sync | OTA integration |
| 6 | Payments (Stripe) + 3DS | Revenue capture |
| 7 | RBAC + audit log | Multi-user safety |
| 8 | i18n + accessibility audit | WCAG AA compliance |

---

## 📝 NOTES

- **Voice is the differentiator** — prioritize WebRTC migration and streaming STT/TTS
- **Guest portal drives retention** — booking mods, comms, loyalty
- **Dashboard is the daily tool** — staff UX > flashy charts
- **OpenLine integration** — keep webhook idempotent; it's the bridge to telephony
- **Test in Docker** — parity between dev and prod environments

---

*Last updated: 2026-11-24*
*Next review: After Sprint 1 completion*