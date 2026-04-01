# SINYAL — Product Requirements Document v2

> **Last updated:** 29 March 2026  
> **Status:** Live (MVP)  
> **Stack:** FastAPI monolith · Supabase (Auth + PostgreSQL) · Scrapling (headless) · Uvicorn  
> **Domain target:** sinyal.id  

---

## 1. Executive Summary

**Sinyal** adalah *content intelligence tool* untuk kreator konten & UMKM Indonesia.  
Bukan cuma scraping — Sinyal menganalisa konten yang perform di TikTok, Instagram, YouTube, X, dan Facebook, lalu memberikan *actionable insight* (hook type, hook score, CTA detection, angle, content idea) yang bisa langsung dieksekusi.

**Core value proposition:**  
*"Tau konten apa yang lagi perform, kenapa perform, dan gimana bikin versi kamu."*

---

## 2. Target Market

| Segment | Deskripsi | Pain Point |
|---------|-----------|------------|
| **Kreator TikTok / IG** | Content creator 5K–500K followers | Kehabisan ide, gak tau hook apa yang works |
| **UMKM / Online seller** | Jualan di Shopee/Tokped, pakai TikTok buat awareness | Gak ngerti konten, cuma post asal-asalan |
| **Social Media Manager** | Handle 3-10 brand, butuh riset cepat | Manual scrolling FYP berjam-jam |
| **Affiliate marketer** | Promote produk di TikTok Shop | Butuh tau angle mana yang convert |

**Geografi:** Indonesia-first (bahasa, harga, payment gateway IDR).

---

## 3. Architecture Overview

### 3.1 Tech Stack

| Layer | Technology |
|-------|-----------|
| **Backend** | Python 3.12 + FastAPI (single `server.py` monolith, ~5891 lines) |
| **Auth** | Supabase Auth (JWT, Google OAuth via Supabase provider) |
| **Database** | Supabase PostgreSQL (profiles, payments, affiliates, saved items, playlists) |
| **Scraping** | Scrapling[all] (AsyncStealthySession, Patchright, Playwright) |
| **Transcript** | `youtube_transcript_api` (YouTube), TikTok `subtitleInfos`/`contents[]`, caption fallback (IG/X/FB) |
| **Payment** | Mayar.id (webhook-based, IDR) |
| **Server** | Uvicorn, single process |
| **Frontend** | Server-rendered HTML (inline in server.py), vanilla JS, no framework |
| **Design** | DM Serif Display + Plus Jakarta Sans, warm palette (`--accent: #c0391b`, `--bg: #faf3ec`) |

### 3.2 File Structure

```
Tiktok-Automation-API/
├── server.py              # Monolith: all routes, HTML, CSS, JS, business logic
├── scraper/
│   ├── __init__.py
│   ├── base.py            # BaseScraper abstract class
│   ├── tiktok.py          # TikTok scraper (search, profile, comments, transcript)
│   ├── youtube.py         # YouTube scraper + real transcript via youtube_transcript_api
│   ├── instagram.py       # Instagram scraper + caption-as-transcript fallback
│   ├── twitter.py         # X/Twitter scraper + tweet-text-as-transcript fallback
│   ├── facebook.py        # Facebook scraper + description-as-transcript fallback
│   └── models.py          # VideoResult dataclass, save_results(), PDF generation
├── requirements.txt       # Dependencies
├── .env                   # Environment configuration
├── run.py                 # CLI runner (legacy)
├── output/                # Generated JSON/CSV/PDF exports
└── docs/
    └── PRD-v2.md          # This document
```

### 3.3 Environment Variables

| Variable | Purpose |
|----------|---------|
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_ANON_KEY` | Supabase anonymous key (client-side auth) |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service role key (server-side admin) |
| `DEV_AUTH_BYPASS` | Skip auth in development (`"true"`/`"false"`) |
| `ADMIN_USERNAME` | Admin dashboard login |
| `ADMIN_PASSWORD` | Admin dashboard password |
| `OWNER_EMAIL` | Owner account email (bypasses Supabase, gets Pro unlimited) |
| `OWNER_PASSWORD` | Owner account password |
| `MAYAR_URL_WEEKLY` | Mayar checkout URL for Weekly tier |
| `MAYAR_URL_CREDIT` | Mayar checkout URL for Credit tier |
| `MAYAR_URL_STARTER` | Mayar checkout URL for Starter tier |
| `MAYAR_URL_PRO` | Mayar checkout URL for Pro tier |
| `MAYAR_URL_LIFETIME` | Mayar checkout URL for Lifetime tier |
| `MAYAR_WEBHOOK_SECRET` | Mayar webhook verification secret |
| `AFFILIATE_COMMISSION_PCT` | Affiliate commission percentage (default: 20%) |
| `AFFILIATE_MIN_PAYOUT_IDR` | Minimum payout threshold (default: Rp50.000) |
| `COOKIE_SECURE` | Set secure flag on cookies (production: `"true"`) |
| `SCRAPE_TIMEOUT_SECONDS` | Per-platform scrape timeout (default: 45s) |
| `LIFETIME_SLOTS_TOTAL` | Max lifetime deal slots (default: 200) |

---

## 4. Features — Current State (v2)

### 4.1 Core Features

#### 🔍 Multi-Platform Search (`/api/search`)
- **Platforms:** TikTok, YouTube, Instagram, X (Twitter), Facebook
- **Parameters:** `q` (keyword, supports multi-keyword via `\n`), `platforms`, `max_results` (1-50), `sort` (relevance/popular/latest/most_liked), `date_range` (all/7d/30d), `min_views`, `max_views`, `min_likes`, `max_likes`
- **Output per result:**
  - Metadata: title, author, views, likes, comments, shares, saves, duration, upload_date, thumbnail, music, hashtags
  - Text: caption, description, transcript (+ transcript_source)
  - **AI Insights:** hook, content, hook_type, hook_score, cta_type, angle, content_idea
- **Caching:** 15-minute in-memory cache (per query + per platform)
- **Export:** JSON, CSV, PDF (with reportlab). Free tier gets watermark.

#### 👤 Profile Scraper (`/api/profile`)
- TikTok only (current)
- Input: `username`, `max_results`, `sort`, `date_range`
- Returns same VideoResult fields + insight enrichment

#### 💬 Comment Scraper (`/api/comments`)
- TikTok only (current)
- Input: `url` (video URL), `max_comments` (1-200)
- Returns structured comment data + `video_comment_count`

#### 📝 Transcript Extraction
| Platform | Method | Source Tag |
|----------|--------|-----------|
| **TikTok** | `contents[].desc` (spoken text) → `subtitleInfos` (subtitle files via httpx) → `imagePost.images[].title` → caption fallback | `spoken_text`, `subtitle:{lang}`, `image_text`, `caption` |
| **YouTube** | `youtube_transcript_api` — manual captions preferred → generated → any language | `manual_caption`, `generated_caption`, `auto_caption` |
| **Instagram** | Caption text (hashtags stripped, min 30 chars) | `caption` |
| **X/Twitter** | Tweet text (URLs stripped, min 20 chars) | `tweet_text` |
| **Facebook** | Video description (min 30 chars) | `caption` |

#### 🧠 AI Insight Engine (rule-based, no LLM)
Derived in `enrich_result_text()` from transcript/caption/description:

| Insight | Logic | Output Example |
|---------|-------|----------------|
| **Hook Type** | Keyword matching on first sentence | Pertanyaan, Shock/Warning, Storytelling/POV, Tips/Tutorial, Review/Unboxing, Statement |
| **Hook Score** | Engagement rate (likes/views %) | 🔥 Sangat Kuat (>8%), 💪 Kuat (>4%), 👍 Cukup (>2%), 📊 Biasa |
| **CTA Type** | Regex patterns on full text | 🛒 Beli/Keranjang, 👤 Follow/Subscribe, 💬 Engagement, 📌 Save, 📩 Direct Message |
| **Angle** | Regex patterns on full text | 💰 Budget-Friendly, 🎯 Honest Review, 🔒 Hidden Gem, 📈 Riding Trend, 🌅 Day-in-My-Life, ⚖️ Perbandingan, 🌱 Untuk Pemula, ✅ Hasil/Proof, 💡 General |
| **Content Idea** | Composite of hook_type + angle + duration + platform | "Buat versi tips / tutorial dengan angle budget-friendly, durasi ~45s, di Tiktok" |

#### 💾 Save & Playlist System
- **Playlists:** CRUD (`/api/saved/playlists`) — name, description, color
- **Saved Items:** CRUD (`/api/saved/items`) — save any VideoResult to a playlist
- Backed by Supabase tables: `saved_playlists`, `saved_items`

#### 📥 Export & Download (`/api/download`)
- Formats: JSON, CSV, PDF
- PDF generated with reportlab (branded Sinyal report)
- Watermark on free-tier exports

### 4.2 Auth System

| Method | Flow |
|--------|------|
| **Email/Password** | `POST /api/auth/signup` → Supabase creates user → `POST /api/auth/signin` → JWT cookies |
| **Google OAuth** | `GET /api/auth/google` → Supabase → Google consent → `GET /auth/callback` → JWT cookies |
| **Owner bypass** | Email + password match `OWNER_EMAIL`/`OWNER_PASSWORD` → deterministic HMAC token `owner:{hash}` → Pro unlimited, no Supabase |
| **Session check** | `GET /api/auth/session` → validates JWT, refreshes if expired |
| **Signout** | `POST /api/auth/signout` → clears cookies |

**Cookies:** `sinyal_access_token` (httponly, 7 days), `sinyal_refresh_token` (httponly, 30 days), SameSite=Lax.

**Referral tracking:** Signup page reads `?ref=CODE` param → stores in `sinyal_ref` cookie → tracked on signup/Google OAuth callback.

### 4.3 Affiliate System

| Endpoint | Function |
|----------|----------|
| `POST /api/affiliate/activate` | Create affiliate account, generate referral code |
| `GET /api/affiliate/me` | Get affiliate info (code, earnings, referrals) |
| `GET /api/affiliate/referrals` | List all referrals with status |
| `GET /api/affiliate/payouts` | Payout history |
| `POST /api/affiliate/payout-settings` | Update bank/e-wallet info |
| `POST /api/affiliate/request-payout` | Request payout (min Rp50.000) |
| `GET /api/affiliate/public-stats` | Public social proof stats |

**Commission flow:**  
User signs up with `?ref=CODE` → referral tracked → when referred user pays → `_credit_affiliate_commission()` triggered in Mayar webhook → affiliate gets X% commission.

**Supabase tables:** `affiliates`, `affiliate_referrals`, `affiliate_payouts`

### 4.4 Payment System (Mayar)

**Webhook:** `POST /api/payment/webhook/mayar`  
- Validates `x-mayar-webhook-secret` header
- On `payment.success`: lookup user by email → upgrade tier → upsert to `payments` and `subscriptions` tables → trigger affiliate commission

**Checkout:** `GET /checkout/{plan_code}` → redirect to Mayar URL (from `env_key` in PLAN_CATALOG)

### 4.5 Admin Dashboard

- **Login:** `GET /admin/login` + `POST /admin/login` (session cookie, 8hr TTL)
- **Dashboard:** `GET /admin` — stats from Supabase:
  - Total users, new today, new this week
  - Users per tier breakdown
  - Recent signups (email, tier, date)
  - Total affiliates, total referrals
  - Pending payout alerts
  - Playlists & saved items count
- **Logout:** `POST /admin/logout`

---

## 5. Pricing Tiers (PLAN_CATALOG)

| Code | Name | Harga | Billing | Key Limits |
|------|------|-------|---------|------------|
| `free` | Free | Rp0 | — | 3 search/hari, TikTok only, preview insight, watermark export |
| `weekly` | Paket 7 Hari | Rp29.000 | 7 hari (no auto-renew) | Unlimited search, TikTok+IG+YT, 10 profil, 10 komentar, 10 analisa AI, no watermark |
| `credit` | Paket 50 Kredit | Rp49.000 | Sekali beli (tidak hangus) | 50 kredit (1 search = 1 kredit), TikTok+IG+YT, profil & komentar = 1 kredit each, no watermark |
| `starter` | Starter | Rp49.000 | Bulanan | 30 search/hari, TikTok+IG, 20 profil/bulan, 20 komentar/bulan, 10 analisa AI, no watermark |
| `pro` | Pro | Rp99.000 | Bulanan | Unlimited semua, 5 platform, Hook/CTA/angle analysis, no watermark |
| `lifetime` | Lifetime Deal | Rp299.000 | 6 bulan (200 slot) | Semua fitur Pro, terbatas 200 slot |

### Limit Enforcement
- **Daily search:** IP-based counter for free, `daily_search_limit` for paid tiers (0 = unlimited)
- **Monthly quotas:** `monthly_profile_limit`, `monthly_comment_limit`, `monthly_transcript_limit`
- **Platform gating:** `allowed_platforms` list per tier
- **Credit system:** `total_credits` for credit tier (1 action = 1 credit)
- **Rate limiting:** Per-IP buckets (search: 30/5min, auth: 10-15/5min)

---

## 6. Pages & Frontend

All pages are server-rendered HTML (inline strings in server.py):

| Route | Page | Description |
|-------|------|-------------|
| `GET /` | Landing page | Hero, features, pricing grid (6 tiers), testimonials, affiliate CTA, footer |
| `GET /start` | Onboarding page | Simplified entry point |
| `GET /signup` | Signup page | Centered card design, email/password + Google OAuth button |
| `GET /signin` | Signin page | Centered card design, email/password + Google OAuth button |
| `GET /app` | Main app | Search bar, platform selector, filters, result cards with insight panels, export buttons, save-to-playlist |
| `GET /account` | Account page | Centered card: tier info, usage stats, upgrade CTA |
| `GET /payment` | Payment/pricing | 6-tier pricing grid with checkout buttons |
| `GET /affiliate` | Affiliate page | Dashboard: referral link, stats, earnings, payout request |
| `GET /admin/login` | Admin login | Dark theme, username/password |
| `GET /admin` | Admin dashboard | Dark theme, stat cards, user table, tier breakdown |

### Frontend Design System
- **Colors:** `--accent: #c0391b`, `--accent-2: #ef5a29`, `--green: #285f58`, `--bg: #faf3ec`, `--surface: #f5ede3`
- **Typography:** DM Serif Display (headings), Plus Jakarta Sans (body)
- **Layout:** Responsive, mobile-first breakpoints
- **Result cards:** Video metadata → transcript toggle → **insight card grid** (hook_type, hook_score, cta_type, angle, content_idea as visual badges)

---

## 7. Data Model

### 7.1 VideoResult (Python dataclass)

```python
@dataclass
class VideoResult:
    # Identifiers
    platform: str
    keyword: str
    video_url: str
    
    # Metadata
    title: str
    author: str
    author_url: str
    views: Optional[int]
    likes: Optional[int]
    comments: Optional[int]
    shares: Optional[int]
    saves: Optional[int]
    duration: Optional[int]
    upload_date: str
    thumbnail: str
    music: str
    hashtags: list
    
    # Text content
    caption: str
    description: str
    transcript: str
    transcript_source: str  # spoken_text, subtitle:{lang}, manual_caption, etc.
    
    # Derived by enrich_result_text()
    hook: str              # First sentence / opening line
    content: str           # Body content summary
    hook_type: str         # Pertanyaan, Shock/Warning, Tips/Tutorial, etc.
    hook_score: str        # 🔥 Sangat Kuat, 💪 Kuat, 👍 Cukup, 📊 Biasa
    cta_type: str          # 🛒 Beli/Keranjang, 👤 Follow, 💬 Engagement, etc.
    angle: str             # 💰 Budget-Friendly, 🎯 Honest Review, etc.
    content_idea: str      # Generated suggestion string
```

### 7.2 Supabase Tables

| Table | Key Columns | Purpose |
|-------|-------------|---------|
| `profiles` | user_id, email, tier, daily_searches_left, created_at | User profiles + tier info |
| `payments` | id, user_id, email, plan_code, amount, provider, status | Payment transactions |
| `subscriptions` | user_id, plan_code, started_at, expires_at, is_active | Active subscriptions |
| `saved_playlists` | id, user_id, name, description, color | User playlists |
| `saved_items` | id, playlist_id, user_id, platform, video_url, title, author, views, likes, hook, transcript, ... | Saved search results |
| `affiliates` | id, user_id, email, referral_code, is_active, commission_pct, lifetime_earnings, paid_out, payout_bank, payout_account | Affiliate accounts |
| `affiliate_referrals` | id, affiliate_id, referred_user_id, referred_email, status, converted_at | Referral tracking |
| `affiliate_payouts` | id, affiliate_id, amount, status, requested_at, paid_at | Payout requests |

---

## 8. API Reference

### Public
| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Landing page |
| GET | `/start` | Onboarding page |
| GET | `/signup` | Signup page |
| GET | `/signin` | Signin page |
| GET | `/payment` | Pricing page |
| GET | `/affiliate` | Affiliate page |
| GET | `/health` | Health check |
| GET | `/app` | Main application |
| GET | `/account` | Account page |
| GET | `/checkout/{plan_code}` | Redirect to Mayar checkout |

### Auth
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/auth/signup` | Register (email, password, optional referral_code) |
| POST | `/api/auth/signin` | Login (email, password) |
| POST | `/api/auth/signout` | Logout (clear cookies) |
| GET | `/api/auth/google` | Initiate Google OAuth |
| GET | `/auth/callback` | OAuth callback handler |
| GET | `/api/auth/session` | Check/refresh session |

### Core API
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/search` | Multi-platform search + insight enrichment |
| GET | `/api/profile` | Profile scraper (TikTok) |
| GET | `/api/comments` | Comment scraper (TikTok) |
| GET | `/api/download?file=` | Download exported file |
| GET | `/api/system/config` | System config (features, Supabase status, Mayar readiness) |

### Account
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/account/usage` | Current usage stats |
| GET | `/api/account/next-step` | Personalized next-step recommendation |
| GET | `/api/billing/plans` | All available plans with checkout URLs |

### Saved / Playlists
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/saved/playlists` | List playlists |
| POST | `/api/saved/playlists` | Create playlist |
| GET | `/api/saved/items` | List saved items (optional playlist_id filter) |
| POST | `/api/saved/items` | Save item to playlist |
| DELETE | `/api/saved/items/{id}` | Remove saved item |

### Affiliate
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/affiliate/me` | Current user's affiliate info |
| POST | `/api/affiliate/activate` | Activate affiliate account |
| GET | `/api/affiliate/referrals` | List referrals |
| GET | `/api/affiliate/payouts` | Payout history |
| POST | `/api/affiliate/payout-settings` | Update bank/e-wallet info |
| POST | `/api/affiliate/request-payout` | Request payout |
| GET | `/api/affiliate/public-stats` | Public aggregate stats |

### Payment
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/payment/webhook/mayar` | Mayar webhook receiver |

### Admin
| Method | Path | Description |
|--------|------|-------------|
| GET | `/admin/login` | Admin login page |
| POST | `/admin/login` | Admin login action |
| POST | `/admin/logout` | Admin logout |
| GET | `/admin` | Admin dashboard |

---

## 9. Security

| Concern | Implementation |
|---------|---------------|
| **Auth cookies** | httponly, SameSite=Lax, 7/30 day TTL |
| **CSRF** | SameSite=Lax cookies + origin checking on mutations |
| **Rate limiting** | IP-based, per-bucket (30 search/5min, 10-15 auth/5min) |
| **Admin** | Separate session cookie, 8hr TTL, username/password |
| **Owner bypass** | HMAC-SHA256 deterministic token, checked server-side |
| **Webhook** | `x-mayar-webhook-secret` header validation |
| **Headers** | Security headers middleware (X-Content-Type-Options, X-Frame-Options, etc.) |
| **Input** | Query param validation via FastAPI/Pydantic |

---

## 10. Dependencies

```
fastapi==0.135.2          # Web framework
uvicorn==0.42.0           # ASGI server
scrapling[all]==0.4.2     # Stealth scraping (includes Playwright, Patchright)
patchright==1.58.2        # Chromium fork for anti-detection
playwright==1.58.0        # Browser automation
httpx==0.28.1             # Async HTTP client
PyJWT==2.12.1             # JWT decoding
cryptography==46.0.5      # Crypto operations
python-dotenv==1.2.2      # Environment variables
pydantic==2.12.5          # Data validation
beautifulsoup4==4.14.3    # HTML parsing
lxml==6.0.2               # Fast XML/HTML parser
orjson==3.11.7            # Fast JSON
reportlab==4.4.1          # PDF generation
youtube_transcript_api    # YouTube transcript extraction
```

---

## 11. Roadmap — What's Next

### Phase 1: Stabilize & Ship (Current → +2 weeks)
- [ ] Deploy to production server (sinyal.id)
- [ ] Configure all Mayar checkout URLs
- [ ] Set `COOKIE_SECURE=true` for production
- [ ] Implement credit system enforcement (deduct on each action)
- [ ] Implement weekly tier expiration check
- [ ] Add Google OAuth client configuration to Supabase
- [ ] Seed Supabase tables (profiles, affiliates, etc.)

### Phase 2: TikTok Shop Affiliate Scraper (+2-4 weeks)
- [ ] Build `scraper/tiktok_shop.py` — scrape product data from videos with keranjang kuning
- [ ] Integrate Indonesian residential proxy for geo-restricted TikTok Shop content
- [ ] Extract: product name, price, jumlah terjual, rating, commission rate
- [ ] Show affiliate product data alongside video results
- [ ] New insight: "Produk affiliate terdeteksi" card

### Phase 3: LLM-Powered Insights (+1-2 months)
- [ ] Replace rule-based insight engine with LLM (Groq free tier / local Whisper)
- [ ] Real transcript for Instagram/Facebook via Whisper API
- [ ] Script generation: "Buat script berdasarkan hook + angle ini"
- [ ] Content calendar suggestions based on trending patterns

### Phase 4: Scale (+3-6 months)
- [ ] Break monolith into microservices (auth, scraping, insights, payment)
- [ ] Add Redis cache layer (replace in-memory)
- [ ] User dashboard: search history, trend tracking over time
- [ ] Team/agency accounts
- [ ] API keys for power users
- [ ] Shopee/Tokopedia integration for e-commerce angle

---

## 12. Appendix: Route Map (All 35 Endpoints)

```
GET  /                          → Landing page
GET  /start                     → Onboarding page
GET  /signup                    → Signup page
GET  /signin                    → Signin page
GET  /app                       → Main application
GET  /account                   → Account page
GET  /payment                   → Pricing page
GET  /affiliate                 → Affiliate dashboard
GET  /checkout/{plan_code}      → Redirect to Mayar
GET  /health                    → Health check
GET  /auth/callback             → OAuth callback

POST /api/auth/signup           → Register
POST /api/auth/signin           → Login
POST /api/auth/signout          → Logout
GET  /api/auth/google           → Google OAuth initiate
GET  /api/auth/session          → Session check

GET  /api/search                → Multi-platform search
GET  /api/profile               → Profile scraper
GET  /api/comments              → Comment scraper
GET  /api/download              → File download

GET  /api/system/config         → System configuration
GET  /api/account/usage         → Usage stats
GET  /api/account/next-step     → Personalized next step
GET  /api/billing/plans         → Plan catalog

GET  /api/saved/playlists       → List playlists
POST /api/saved/playlists       → Create playlist
GET  /api/saved/items           → List saved items
POST /api/saved/items           → Save item
DEL  /api/saved/items/{id}      → Delete saved item

GET  /api/affiliate/me          → Affiliate info
POST /api/affiliate/activate    → Activate affiliate
GET  /api/affiliate/referrals   → List referrals
GET  /api/affiliate/payouts     → Payout history
POST /api/affiliate/payout-settings → Update payout info
POST /api/affiliate/request-payout  → Request payout
GET  /api/affiliate/public-stats    → Public stats

POST /api/payment/webhook/mayar → Mayar webhook

GET  /admin/login               → Admin login page
POST /admin/login               → Admin login action
POST /admin/logout              → Admin logout
GET  /admin                     → Admin dashboard
```

---

## 13. Diff dari PRD v1

| Area | PRD v1 | PRD v2 (Current) |
|------|--------|-------------------|
| **Tiers** | 4 tiers (free, starter, pro, lifetime) | **6 tiers** (+weekly Rp29.000, +credit 50 kredit Rp49.000) |
| **Pricing copy** | "10 transkrip video" | "10 analisa konten AI" |
| **Insight engine** | Hook + content only | **+hook_type, hook_score, cta_type, angle, content_idea** |
| **Transcript** | TikTok only | **All 5 platforms** (YouTube real, others fallback) |
| **YouTube transcript** | Not implemented | **youtube_transcript_api** (manual > generated > any) |
| **Frontend** | Basic result cards | **Insight card grid** (visual badges per result) |
| **VideoResult model** | 18 fields | **23 fields** (+5 insight fields) |
| **Pricing framing** | Tool-focused ("transkrip") | **Outcome-focused** ("analisa konten AI") |
| **Google OAuth** | Not implemented | **Full flow** (Supabase provider, PKCE + implicit + hash fragment handling) |
| **Credit system** | Not planned | **Defined** (credit tier in PLAN_CATALOG, enforcement TBD) |
| **Weekly tier** | Not planned | **Defined** (7-day pass, no auto-renew) |

---

*PRD v2 — Sinyal Content Intelligence — March 2026*
