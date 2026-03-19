# SECURITY.md

Security baseline for `Playground`.

## Goal
Build `Playground` as a public-facing SaaS with sane, practical security defaults from the start.

Important: no system is safe from **all** hacks. The goal is to reduce realistic risk, limit blast radius, and make failures detectable + recoverable.

---

## Threat Model

### What we need to protect
- User accounts and sessions
- Billing/subscription state
- Search/profile/comment query history
- Exported research artifacts
- API endpoints and scraper infrastructure
- Environment secrets (Supabase, Mayar, API keys)

### Main risks
- Broken auth/session handling
- Insecure file download paths
- Weak webhook validation
- Rate-limit abuse / scraper abuse
- Prompt or input injection into rendered HTML
- Cross-site scripting (XSS)
- CSRF on state-changing routes
- Leaking internal config/dev state to users
- Multi-tenant data leaks between users/teams
- Unbounded scrape costs / denial-of-wallet

---

## Security Principles

1. **Default deny** for paid/protected features
2. **Server-side enforcement** for auth, access, and quota
3. **Least privilege** everywhere
4. **No trust in frontend**
5. **Escape and sanitize all user-controlled output**
6. **Keep secrets out of client code**
7. **Separate public UI from internal/debug/admin flows**
8. **Log important security events**
9. **Fail closed** where possible
10. **Minimize blast radius** when scrapers or billing fail

---

## Required Security Controls

### 1. Authentication & Session Security
- Use Supabase Auth properly for login/session handling
- Use secure, httpOnly cookies where possible
- Set `Secure`, `HttpOnly`, and `SameSite=Lax` or stricter on session cookies
- Enforce server-side auth checks on protected routes
- Add logout route that clears all relevant auth cookies/tokens
- Do not expose internal auth readiness/config details in user-facing UI

### 2. Authorization
- Every protected API route must verify authenticated user server-side
- Every paid feature must enforce subscription/plan check server-side
- Team/org membership checks must be enforced server-side
- Never trust client-supplied plan/role/quota values

### 3. CSRF Protection
- Add CSRF protection for state-changing POST routes if cookie-based auth is used
- Especially important for:
  - signout
  - billing/account actions
  - saved search creation
  - any future settings/profile updates

### 4. XSS Protection
- Escape all user-controlled or scraped content before rendering into HTML
- Scraped titles, captions, comments, transcripts, author names must always be escaped
- Avoid `innerHTML` where possible; prefer `textContent`
- If HTML injection is necessary, sanitize first

### 5. File Download Safety
- `GET /api/download` must never allow arbitrary file read
- Restrict downloads to a known safe output directory allowlist
- Reject absolute paths, `..`, symlinks, and path traversal patterns
- Consider attaching files to authenticated user/job ownership in future

### 6. Billing / Webhook Security
- Validate Mayar webhook secret strictly
- Verify webhook payload shape and expected fields
- Make webhook processing idempotent
- Log webhook events and duplicate attempts
- Do not trust payment callbacks without verification
- Separate payment success UI from actual subscription activation logic

### 7. Rate Limiting & Abuse Control
- Rate-limit public endpoints:
  - auth routes
  - search
  - profile
  - comments
  - transcript endpoints
- Add IP/user-based limits
- Add stricter limits for unauthenticated users
- Consider queueing expensive scrape jobs

### 8. Input Validation
- Validate URLs, usernames, keyword lengths, max counts, and platform values
- Cap search keyword count and request complexity
- Reject malformed TikTok/profile URLs early
- Enforce bounds on `max_results`, `max_comments`, etc.

### 9. Output / Data Exposure
- Do not expose internal config readiness in UI
- Do not expose stack details unnecessarily
- Avoid returning raw internal exception traces to clients
- Keep debug output server-side only

### 10. Headers & Browser Security
Set security headers:
- `Content-Security-Policy`
- `X-Frame-Options: DENY`
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Permissions-Policy` minimal set
- `Strict-Transport-Security` (when HTTPS is in use)

### 11. Secrets Management
- Keep secrets only in environment variables / secret manager
- Never commit API keys or webhook secrets
- Rotate leaked/test secrets immediately
- Separate dev/staging/prod secrets

### 12. Data Isolation
- Queries, exports, saved searches, and future reports must be scoped to the current user/team
- Never trust client-side filtering for access control
- If using Supabase RLS later, design for it explicitly

### 13. Logging & Monitoring
Log these server-side:
- auth failures
- webhook failures
- suspicious download attempts
- repeated scrape abuse
- repeated invalid parameter abuse
- unexpected 5xx spikes
- scraper provider/platform failures

### 14. Admin / Internal Separation
- Internal/admin/debug tools must not appear in default user-facing UI
- Internal config/status pages should be env-protected or hidden
- Never leak dev state or internal architecture notes to end users

---

## Security Checklist for Current Repo

### Immediate Priority
- [ ] Remove internal/dev/config messaging from user-facing pages
- [ ] Restrict `/api/download` to safe output paths only
- [ ] Add centralized input validation helpers
- [ ] Add security headers middleware
- [ ] Add basic per-route rate limiting
- [ ] Review auth cookie/session behavior
- [ ] Make webhook processing idempotent
- [ ] Audit any `innerHTML` usage fed by scraped data

### Next Priority
- [ ] Add CSRF protection if cookie-auth flows are retained
- [ ] Add audit logging for auth + billing + download events
- [ ] Add team/user ownership checks for exports and saved searches
- [ ] Add abuse protections for expensive scrape routes

### Nice to Have Later
- [ ] Queue-based scraping workers
- [ ] IP reputation / bot detection
- [ ] anomaly alerts
- [ ] staged environment hardening checklist

---

## What “safe enough to sell” means
Before public launch, minimum acceptable:
- Auth works and is server-enforced
- Billing is verified server-side
- Internal/debug UI is gone from user-facing flows
- Download path traversal is impossible
- Scraped content is escaped/sanitized
- Rate limiting exists
- Security headers exist
- No secrets in repo/client
- Basic logs exist for auth, billing, and abuse events

---

## Product Positioning Security Note
Because `Playground` is a **content intelligence tool**, not a spy/revenue estimator, avoid risky dark-pattern features:
- no private data scraping
- no implied secret-access language
- no fake precision in estimates
- no “hack the algorithm” framing

Security is also a trust signal in positioning.
