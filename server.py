#!/usr/bin/env python3
import asyncio
import base64
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
from pathlib import Path
from urllib.parse import quote

from fastapi import Body, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse, StreamingResponse
from dotenv import load_dotenv
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from scraper.tiktok import TikTokScraper
from scraper.youtube import YouTubeScraper
from scraper.instagram import InstagramScraper
from scraper.twitter import TwitterScraper
from scraper.facebook import FacebookScraper
from scraper.kalodata import KalodataScraper
from scraper.models import VideoResult, save_results
from scraper.tiktok_shop import TikTokShopScraper, TikTokProduct

load_dotenv()

app = FastAPI(
  title="Sinyal - Content Intelligence",
  docs_url=None,
  redoc_url=None,
  openapi_url=None,
)
SCRAPE_TIMEOUT_SECONDS = int(os.getenv("SCRAPE_TIMEOUT_SECONDS", "90"))
_BROWSER_SEM: asyncio.Semaphore | None = None  # lazy-init inside event loop


def _get_browser_sem() -> asyncio.Semaphore:
    """Return (and lazily create) the per-event-loop browser semaphore.
    Max 3 concurrent Playwright browsers to prevent CPU/memory contention.
    """
    global _BROWSER_SEM
    if _BROWSER_SEM is None:
        _BROWSER_SEM = asyncio.Semaphore(3)
    return _BROWSER_SEM
OUTPUT_DIR = Path("output").resolve()
PROFILE_CACHE_TTL_SECONDS = int(os.getenv("PROFILE_CACHE_TTL_SECONDS", "900"))
SEARCH_CACHE_TTL_SECONDS = int(os.getenv("SEARCH_CACHE_TTL_SECONDS", "900"))
COMMENTS_CACHE_TTL_SECONDS = int(os.getenv("COMMENTS_CACHE_TTL_SECONDS", "900"))
KALODATA_COUNTRY = (os.getenv("KALODATA_COUNTRY", "id") or "id").strip().lower()
KALODATA_REGION_LABEL = {
  "id": "Indonesia",
  "vn": "Vietnam",
  "th": "Thailand",
  "my": "Malaysia",
  "ph": "Philippines",
}.get(KALODATA_COUNTRY, KALODATA_COUNTRY.upper())
KALODATA_TIMEZONE = ZoneInfo(os.getenv("KALODATA_TIMEZONE", "Asia/Jakarta"))
KALODATA_DASHBOARD_CACHE_TTL_SECONDS = int(os.getenv("KALODATA_DASHBOARD_CACHE_TTL_SECONDS", "600"))
AUTH_COOKIE_NAME = "sinyal_access_token"
REFRESH_COOKIE_NAME = "sinyal_refresh_token"
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"
DEV_AUTH_BYPASS = os.getenv("DEV_AUTH_BYPASS", "false").lower() == "true"
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "changeme123")
ADMIN_COOKIE_NAME = "sinyal_admin_session"
OWNER_EMAIL = os.getenv("OWNER_EMAIL", "")
OWNER_PASSWORD = os.getenv("OWNER_PASSWORD", "")
OWNER_USER_ID = "owner-local-00000000"
# in-memory admin sessions: {token: expires_timestamp}
_admin_sessions: dict[str, float] = {}
PROFILE_CACHE: dict[tuple[str, int, str], tuple[float, dict]] = {}
SEARCH_CACHE: dict[tuple, tuple[float, dict]] = {}
COMMENTS_CACHE: dict[tuple[str, int], tuple[float, dict]] = {}
PLATFORM_SEARCH_CACHE: dict[tuple, tuple[float, list]] = {}
KALODATA_DASHBOARD_CACHE: dict[tuple[str, str, int], tuple[float, dict]] = {}
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
RATE_LIMIT_BUCKETS: dict[str, list[float]] = {}
RATE_LIMIT_RULES = {
    "auth_signup": (10, 300),
    "auth_signin": (15, 300),
  "app": (60, 300),
    "search": (30, 300),
  "dashboard": (20, 300),
    "profile": (30, 300),
    "comments": (20, 300),
}
APP_GUARD_COOKIE_NAME = "sinyal_app_guard"
APP_GUARD_HEADER_NAME = "x-sinyal-app-guard"
APP_GUARD_SECRET = os.getenv("APP_GUARD_SECRET", "sinyal-app-guard-secret")
APP_GUARD_TTL_SECONDS = int(os.getenv("APP_GUARD_TTL_SECONDS", "86400"))
MAX_REQUEST_BYTES = int(os.getenv("MAX_REQUEST_BYTES", "1048576"))
ALLOWED_HTTP_METHODS = {"GET", "POST", "DELETE", "HEAD", "OPTIONS"}
ALLOWED_HOSTS = [host.strip() for host in os.getenv("ALLOWED_HOSTS", "").split(",") if host.strip()]
HTML_GUARD_PATHS = {"/", "/signin", "/signup", "/app", "/account", "/affiliate", "/payment"}
PROTECTED_API_PREFIXES = (
  "/api/search",
  "/api/profile",
  "/api/comments",
  "/api/products",
  "/api/dashboard/trending",
  "/api/account/usage",
  "/api/saved",
  "/api/affiliate/me",
  "/api/affiliate/activate",
  "/api/affiliate/payout-settings",
  "/api/affiliate/request-payout",
  "/api/affiliate/referrals",
  "/api/affiliate/payouts",
  "/api/download",
)
BOT_UA_MARKERS = (
  "python-requests",
  "python-httpx",
  "curl/",
  "wget",
  "scrapy",
  "aiohttp",
  "httpclient",
  "go-http-client",
  "okhttp",
  "postmanruntime",
  "headlesschrome",
  "phantomjs",
  "selenium",
  "playwright",
  "puppeteer",
)

if ALLOWED_HOSTS:
  app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
  return JSONResponse(
    {"error": "Request tidak valid.", "code": "bad_request"},
    status_code=422,
  )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
  detail = exc.detail if isinstance(exc.detail, str) else "Request gagal diproses."
  return JSONResponse(
    {"error": detail, "code": "http_error"},
    status_code=exc.status_code,
  )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
  print(f"[UnhandledError] {request.method} {request.url.path}: {exc}")
  return JSONResponse(
    {"error": "Terjadi gangguan pada server. Coba lagi sebentar.", "code": "server_error"},
    status_code=500,
  )


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    path = request.url.path or "/"
    if request.method.upper() not in ALLOWED_HTTP_METHODS:
        return JSONResponse(
            {"error": "Method tidak diizinkan.", "code": "method_not_allowed"},
            status_code=405,
        )

    content_length = request.headers.get("content-length") or ""
    if content_length.isdigit() and int(content_length) > MAX_REQUEST_BYTES:
        return JSONResponse(
            {"error": "Payload terlalu besar.", "code": "payload_too_large"},
            status_code=413,
        )

    if path == "/app":
        rate_limited = enforce_rate_limit(request, "app")
        if rate_limited:
            return rate_limited

    protection_error = _enforce_request_guard(request)
    if protection_error:
        return protection_error

    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Server"] = "Sinyal"
    response.headers["X-Powered-By"] = "Sinyal"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive, nosnippet, noimageindex"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "img-src 'self' data: https:; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://fonts.gstatic.com; "
        "font-src 'self' https://fonts.gstatic.com data:; "
        "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com; "
        "connect-src 'self' https:; "
        "frame-ancestors 'none'; "
        "base-uri 'self';"
    )
    if path.startswith("/api/") or path in HTML_GUARD_PATHS:
        response.headers["Cache-Control"] = "no-store, private"
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if request.method == "GET" and path in HTML_GUARD_PATHS:
        token = _make_app_guard_token(request)
        if token:
            response.set_cookie(
                APP_GUARD_COOKIE_NAME,
                token,
                max_age=APP_GUARD_TTL_SECONDS,
                secure=COOKIE_SECURE,
                httponly=False,
                samesite="lax",
                path="/",
            )
    return response

SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "") or os.getenv("SUPABASE_PUBLISHABLE_KEY", "")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

SCRAPERS = {
    "tiktok": TikTokScraper,
    "youtube": YouTubeScraper,
    "instagram": InstagramScraper,
    "twitter": TwitterScraper,
    "facebook": FacebookScraper,
}

PLAN_CATALOG = {
    "free": {
        "name": "Free",
        "price_idr": 0,
        "tagline": "Coba dulu gratis — rasakan hasilnya, bukan cuma interfacenya.",
        "limits": [
            "3 pencarian per hari",
            "TikTok saja",
            "Daily Briefing (preview)",
            "1 Content Autopsy / minggu",
            "Watermark di export",
        ],
        "cta": "Mulai Gratis",
        "env_key": "",
        "accent": "muted",
        "daily_search_limit": 3,
        "monthly_search_limit": 0,
        "monthly_profile_limit": 0,
        "monthly_comment_limit": 0,
        "monthly_transcript_limit": 0,
        "allowed_platforms": ["tiktok"],
        "watermark_exports": True,
        "billing_interval": "free",
    },
    "weekly": {
        "name": "Paket 7 Hari",
        "price_idr": 29_000,
        "tagline": "Akses penuh selama 7 hari. Tanpa auto-renew.",
        "limits": [
            "Unlimited pencarian selama 7 hari",
            "TikTok + Instagram + YouTube",
            "10 cek profil",
            "10 tarik komentar",
            "10 analisa konten AI",
            "Export tanpa watermark",
        ],
        "cta": "Beli Paket 7 Hari",
        "env_key": "MAYAR_URL_WEEKLY",
        "accent": "teal",
        "daily_search_limit": 0,
        "monthly_search_limit": 0,
        "monthly_profile_limit": 10,
        "monthly_comment_limit": 10,
        "monthly_transcript_limit": 10,
        "allowed_platforms": ["tiktok", "instagram", "youtube"],
        "watermark_exports": False,
        "billing_interval": "weekly",
        "duration_days": 7,
    },
    "credit": {
        "name": "Paket 50 Kredit",
        "price_idr": 49_000,
        "tagline": "Beli sekali, pakai kapan saja. Tidak hangus.",
        "limits": [
            "50 kredit (1 search = 1 kredit)",
            "TikTok + Instagram + YouTube",
            "Profil & komentar masing-masing 1 kredit",
            "Analisa konten AI termasuk",
            "Export tanpa watermark",
        ],
        "cta": "Beli 50 Kredit",
        "env_key": "MAYAR_URL_CREDIT",
        "accent": "blue",
        "daily_search_limit": 0,
        "monthly_search_limit": 0,
        "monthly_profile_limit": 0,
        "monthly_comment_limit": 0,
        "monthly_transcript_limit": 0,
        "allowed_platforms": ["tiktok", "instagram", "youtube"],
        "watermark_exports": False,
        "billing_interval": "credit",
        "total_credits": 50,
    },
    "starter": {
        "name": "Starter",
        "price_idr": 49_000,
      "tagline": "Riset konten harian + insight yang siap dieksekusi setiap hari.",
        "limits": [
            "30 pencarian per hari",
            "TikTok + Instagram",
            "Full Daily Briefing",
            "100 insight per bulan",
            "10 Content Autopsy per bulan",
            "1 Niche Playbook",
            "20 cek profil per bulan",
            "20 tarik komentar per bulan",
            "10 analisa konten AI",
        ],
        "cta": "Ambil Starter",
        "env_key": "MAYAR_URL_STARTER",
        "accent": "sun",
        "daily_search_limit": 30,
        "monthly_search_limit": 0,
        "monthly_profile_limit": 20,
        "monthly_comment_limit": 20,
        "monthly_transcript_limit": 10,
        "allowed_platforms": ["tiktok", "instagram"],
        "watermark_exports": False,
        "billing_interval": "monthly",
    },
    "pro": {
        "name": "Pro",
        "price_idr": 99_000,
      "tagline": "Akses penuh ke semua platform — daily intelligence tanpa batas.",
        "limits": [
            "Pencarian unlimited",
            "Semua platform (TikTok, IG, YouTube, X, Facebook)",
            "Full Daily Briefing",
            "Unlimited Content Autopsy",
            "Semua Niche Playbook",
            "Unlimited profil & komentar",
            "Unlimited analisa konten AI",
            "Hook, CTA & angle analysis",
        ],
        "cta": "Upgrade ke Pro",
        "env_key": "MAYAR_URL_PRO",
        "accent": "ember",
        "daily_search_limit": 0,
        "monthly_search_limit": 0,
        "monthly_profile_limit": 0,
        "monthly_comment_limit": 0,
        "monthly_transcript_limit": 0,
        "allowed_platforms": ["tiktok", "instagram", "youtube", "twitter", "facebook"],
        "watermark_exports": False,
        "billing_interval": "monthly",
    },
}

FREE_DAILY_SEARCH_LIMIT = 3
IP_DAILY_SEARCH_COUNTS: dict[str, tuple[str, int]] = {}  # ip -> (date_str, count)


MONTHLY_USAGE_COUNTERS: dict[tuple[str, str, str], int] = {}  # (user_id, feature, YYYY-MM)


def _current_month_key() -> str:
  return datetime.now(timezone.utc).strftime("%Y-%m")


def _get_profile_usage_field(feature: str) -> str:
  mapping = {
    "profile": "monthly_profiles_used",
    "comments": "monthly_comments_used",
    "transcript": "monthly_transcripts_used",
    "search": "monthly_searches_used",
  }
  return mapping.get(feature, f"monthly_{feature}_used")


def get_monthly_usage(user_id: str, feature: str, profile: dict | None = None) -> int:
  profile = profile or {}
  month_key = _current_month_key()
  memory_used = MONTHLY_USAGE_COUNTERS.get((user_id, feature, month_key), 0)
  profile_used = int(profile.get(_get_profile_usage_field(feature), 0) or 0)
  return max(memory_used, profile_used)


async def increment_monthly_usage(user_id: str, feature: str, amount: int = 1) -> int:
  month_key = _current_month_key()
  key = (user_id, feature, month_key)
  new_value = MONTHLY_USAGE_COUNTERS.get(key, 0) + max(1, amount)
  MONTHLY_USAGE_COUNTERS[key] = new_value
  # Persist to Supabase if configured
  if supabase_rest_configured():
    db_field = _get_profile_usage_field(feature)
    try:
      await supabase_rest_request(
        "PATCH",
        f"/rest/v1/profiles?id=eq.{user_id}",
        payload={db_field: new_value},
      )
    except Exception as e:
      print(f"[WARN] increment_monthly_usage supabase patch failed: {e}")
  return new_value


def normalize_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def split_sentences(value: str) -> list[str]:
    return [part.strip() for part in SENTENCE_SPLIT_RE.split(normalize_text(value)) if part.strip()]


def truncate_words(value: str, max_words: int) -> str:
    words = normalize_text(value).split()
    if len(words) <= max_words:
        return " ".join(words)
    return " ".join(words[:max_words]).rstrip(",.;:") + "..."


def derive_hook(result) -> str:
    candidates = [
        getattr(result, "hook", ""),
        getattr(result, "title", ""),
        getattr(result, "transcript", ""),
        getattr(result, "caption", ""),
        getattr(result, "description", ""),
    ]
    for candidate in candidates:
        candidate = normalize_text(candidate)
        if not candidate:
            continue
        sentences = split_sentences(candidate)
        first = sentences[0] if sentences else candidate
        return truncate_words(first, 16)
    return ""


def derive_content(result, hook: str) -> str:
    transcript = normalize_text(getattr(result, "transcript", ""))
    caption = normalize_text(getattr(result, "caption", ""))
    description = normalize_text(getattr(result, "description", ""))
    title = normalize_text(getattr(result, "title", ""))
    candidates = [transcript, caption, description, title]
    for candidate in candidates:
        if not candidate:
            continue
        sentences = split_sentences(candidate)
        if len(sentences) > 1:
            remaining = " ".join(sentences[1:3]).strip()
            if remaining:
                return truncate_words(remaining, 38)
        if candidate != hook:
            return truncate_words(candidate, 38)
    return ""


def enrich_result_text(result):
    title = normalize_text(getattr(result, "title", ""))
    description = normalize_text(getattr(result, "description", ""))
    caption = normalize_text(getattr(result, "caption", "")) or description
    transcript = normalize_text(getattr(result, "transcript", ""))

    if transcript and caption and transcript.lower() == caption.lower():
        transcript = ""
    if transcript and title and transcript.lower() == title.lower():
        transcript = ""

    result.title = title
    result.description = description
    result.caption = caption
    result.transcript = transcript
    if transcript and not getattr(result, "transcript_source", ""):
        result.transcript_source = "spoken_text"

    hook = derive_hook(result)
    result.hook = hook
    result.content = derive_content(result, hook)

    # ── Derive insight fields ──
    transcript = normalize_text(getattr(result, "transcript", ""))
    caption = normalize_text(getattr(result, "caption", ""))
    description = normalize_text(getattr(result, "description", ""))
    best_text = transcript or caption or description

    # Hook type classification
    hook_lower = hook.lower() if hook else ""
    if any(w in hook_lower for w in ["?", "gimana", "kenapa", "apa", "kapan", "siapa", "how", "why", "what"]):
        result.hook_type = "Pertanyaan"
    elif any(w in hook_lower for w in ["jangan", "stop", "awas", "bahaya", "salah", "fatal"]):
        result.hook_type = "Shock / Warning"
    elif any(w in hook_lower for w in ["pov", "aku", "gue", "saya", "cerita", "story"]):
        result.hook_type = "Storytelling / POV"
    elif any(w in hook_lower for w in ["tips", "cara", "rahasia", "trik", "tutorial", "hack"]):
        result.hook_type = "Tips / Tutorial"
    elif any(w in hook_lower for w in ["review", "honest", "jujur", "coba", "unboxing", "test"]):
        result.hook_type = "Review / Unboxing"
    else:
        result.hook_type = "Statement"

    # Hook score (simple heuristic: engagement-based)
    views = getattr(result, "views", 0) or 0
    likes = getattr(result, "likes", 0) or 0
    engagement_rate = (likes / views * 100) if views > 0 else 0
    if engagement_rate > 8:
        result.hook_score = "🔥 Sangat Kuat"
    elif engagement_rate > 4:
        result.hook_score = "💪 Kuat"
    elif engagement_rate > 2:
        result.hook_score = "👍 Cukup"
    else:
        result.hook_score = "📊 Biasa"

    # CTA detection
    cta_patterns = [
        (r"(?:link|keranjang|shop|beli|order|checkout|klik|tap|cart)", "🛒 Beli / Keranjang"),
        (r"(?:follow|ikutin|subscribe|langganan)", "👤 Follow / Subscribe"),
        (r"(?:comment|komen|tulis|jawab|share|bagikan)", "💬 Engagement"),
        (r"(?:save|simpan|bookmark)", "📌 Save"),
        (r"(?:dm|chat|hubungi|kontak|wa|whatsapp)", "📩 Direct Message"),
    ]
    cta_detected = "Tidak terdeteksi"
    for pattern, label in cta_patterns:
        if re.search(pattern, best_text.lower()):
            cta_detected = label
            break
    result.cta_type = cta_detected

    # Angle extraction
    angle_patterns = [
        (r"(?:murah|hemat|budget|terjangkau|affordable|harga)", "💰 Budget-Friendly"),
        (r"(?:jujur|honest|real|sebenarnya|fakta)", "🎯 Honest Review"),
        (r"(?:rahasia|secret|gak banyak yang tau|tersembunyi|hidden)", "🔒 Rahasia / Hidden Gem"),
        (r"(?:viral|trending|fyp|rame|booming)", "📈 Riding Trend"),
        (r"(?:sehari-hari|daily|routine|pagi|malam|rutin)", "🌅 Day-in-My-Life"),
        (r"(?:vs|banding|perbandingan|compare|mending)", "⚖️ Perbandingan"),
        (r"(?:pemula|newbie|pertama kali|baru mulai|starter)", "🌱 Untuk Pemula"),
        (r"(?:result|hasilnya|before|after|bukti|testimoni|proof)", "✅ Hasil / Proof"),
    ]
    angle = "💡 General"
    for pattern, label in angle_patterns:
        if re.search(pattern, best_text.lower()):
            angle = label
            break
    result.angle = angle

    # Content idea suggestion based on what performed
    duration = getattr(result, "duration", 0) or 0
    dur_label = f"{duration}s" if duration else "?"
    platform = getattr(result, "platform", "")
    result.content_idea = f"Buat versi {result.hook_type.lower()} dengan angle {angle.split(' ', 1)[-1].lower()}, durasi ~{dur_label}, di {platform.title()}"

    return result


def format_idr(value: int) -> str:
    return f"Rp{value:,.0f}".replace(",", ".")


def get_plan_catalog():
    plans = []
    for code, plan in PLAN_CATALOG.items():
        plans.append(
            {
                **plan,
                "code": code,
                "price_label": format_idr(plan["price_idr"]),
                "checkout_url": os.getenv(plan["env_key"], "").strip(),
            }
        )
    return plans


AUTH_COOKIE_MAX_AGE = 60 * 60 * 24 * 7      # 7 days
REFRESH_COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days


def set_auth_cookies(response, access_token: str | None, refresh_token: str | None):
    if access_token:
        response.set_cookie(AUTH_COOKIE_NAME, access_token, httponly=True, samesite="lax", secure=COOKIE_SECURE, max_age=AUTH_COOKIE_MAX_AGE)
    if refresh_token:
        response.set_cookie(REFRESH_COOKIE_NAME, refresh_token, httponly=True, samesite="lax", secure=COOKIE_SECURE, max_age=REFRESH_COOKIE_MAX_AGE)


def supabase_auth_configured() -> bool:
    return bool(SUPABASE_URL and SUPABASE_ANON_KEY)


def supabase_rest_configured() -> bool:
    return bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY)


def supabase_headers(api_key: str | None = None) -> dict[str, str]:
    key = api_key or SUPABASE_ANON_KEY
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


async def supabase_auth_request(path: str, payload: dict, api_key: str | None = None):
    if not supabase_auth_configured():
        return 503, {"error": "Supabase auth belum dikonfigurasi di environment."}

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"{SUPABASE_URL}/auth/v1{path}",
            headers=supabase_headers(api_key),
            json=payload,
        )
    try:
        data = response.json()
    except ValueError:
        data = {"error": response.text}
    return response.status_code, data


def decode_jwt_payload(token: str) -> dict:
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        payload = parts[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload.encode()).decode())
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
        return {}


import hmac as _hmac


def _owner_token() -> str:
    """Deterministic token derived from owner credentials. Never changes."""
    if not OWNER_EMAIL or not OWNER_PASSWORD:
        return ""
    raw = f"{OWNER_EMAIL}:{OWNER_PASSWORD}:sinyal-owner"
    sig = _hmac.new(b"sinyal-owner-secret", raw.encode(), "sha256").hexdigest()
    return f"owner:{sig}"


def _is_owner_token(token: str) -> bool:
    expected = _owner_token()
    return bool(expected and token == expected)


def _base64_url_encode(value: str) -> str:
  return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def _base64_url_decode(value: str) -> str:
  padding = "=" * (-len(value) % 4)
  return base64.urlsafe_b64decode((value + padding).encode()).decode()


def _app_guard_day() -> str:
  return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _build_app_guard_token(ip: str, user_agent: str, day: str | None = None) -> str:
  token_day = day or _app_guard_day()
  payload = f"{ip}|{user_agent}|{token_day}"
  signature = _hmac.new(APP_GUARD_SECRET.encode(), payload.encode(), "sha256").hexdigest()
  return _base64_url_encode(f"{token_day}:{signature}")


def _make_app_guard_token(request: Request) -> str:
  ip = get_client_ip(request)
  user_agent = (request.headers.get("user-agent") or "").strip()
  if not ip or not user_agent:
    return ""
  return _build_app_guard_token(ip, user_agent)


def _has_valid_app_guard(request: Request) -> bool:
  cookie_token = (request.cookies.get(APP_GUARD_COOKIE_NAME) or "").strip()
  header_token = (request.headers.get(APP_GUARD_HEADER_NAME) or "").strip()
  if not cookie_token or not header_token or cookie_token != header_token:
    return False
  expected = _make_app_guard_token(request)
  if not expected:
    return False
  return _hmac.compare_digest(cookie_token, expected)


def _is_same_origin_request(request: Request) -> bool:
  origin = (request.headers.get("origin") or "").strip()
  referer = (request.headers.get("referer") or "").strip()
  expected_origin = f"{request.url.scheme}://{request.url.netloc}"
  if origin:
    return origin == expected_origin
  if referer:
    return referer.startswith(expected_origin)
  sec_fetch_site = (request.headers.get("sec-fetch-site") or "").strip().lower()
  return sec_fetch_site in {"", "same-origin", "same-site", "none"}


def _looks_like_bot(request: Request) -> bool:
  user_agent = (request.headers.get("user-agent") or "").strip().lower()
  if not user_agent:
    return True
  if "testclient" in user_agent:
    return False
  return any(marker in user_agent for marker in BOT_UA_MARKERS)


def _enforce_request_guard(request: Request) -> JSONResponse | None:
  path = request.url.path or "/"
  if not any(path.startswith(prefix) for prefix in PROTECTED_API_PREFIXES):
    return None
  if _looks_like_bot(request):
    return JSONResponse({"error": "Access denied.", "code": "blocked"}, status_code=403)
  if not _is_same_origin_request(request):
    return JSONResponse({"error": "Origin not allowed.", "code": "origin_blocked"}, status_code=403)
  if not _has_valid_app_guard(request):
    return JSONResponse({"error": "Session validation failed.", "code": "guard_required"}, status_code=403)
  return None


def _parse_date_or_none(value: str | None) -> datetime | None:
  if not value:
    return None
  try:
    return datetime.strptime(value, "%Y-%m-%d")
  except ValueError:
    return None


def _resolve_dashboard_window(
  preset: str = "today",
  start_date: str | None = None,
  end_date: str | None = None,
) -> tuple[str, str, str]:
  today_dt = datetime.now(KALODATA_TIMEZONE).date()
  preset_days = {"today": 0, "7d": 6, "30d": 29}
  preset_key = (preset or "today").strip().lower()

  start_dt = _parse_date_or_none(start_date)
  end_dt = _parse_date_or_none(end_date)
  if start_dt and not end_dt:
    end_dt = start_dt
  if end_dt and not start_dt:
    start_dt = end_dt

  if not start_dt or not end_dt:
    days = preset_days.get(preset_key, 0)
    end_dt = today_dt
    start_dt = today_dt - timedelta(days=days)
  else:
    start_dt = start_dt.date()
    end_dt = end_dt.date()

  if start_dt > end_dt:
    start_dt, end_dt = end_dt, start_dt
  if end_dt > today_dt:
    end_dt = today_dt
  if start_dt > today_dt:
    start_dt = today_dt

  span_days = (end_dt - start_dt).days
  if span_days > 89:
    start_dt = end_dt - timedelta(days=89)
    span_days = 89

  if span_days <= 0:
    label = "Hari ini"
  elif span_days == 6:
    label = "7 hari terakhir"
  elif span_days == 29:
    label = "30 hari terakhir"
  else:
    label = f"{span_days + 1} hari terakhir"

  return start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d"), label


@app.get("/robots.txt")
async def robots_txt():
  content = "User-agent: *\nDisallow: /\n"
  return HTMLResponse(content=content, media_type="text/plain")


async def get_authenticated_user(request: Request) -> dict | None:
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token:
        return None

    # ── Owner bypass ──
    if _is_owner_token(token):
        return {"id": OWNER_USER_ID, "email": OWNER_EMAIL, "raw": {}}

    if not supabase_auth_configured():
        return None

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            f"{SUPABASE_URL}/auth/v1/user",
            headers={
                "apikey": SUPABASE_ANON_KEY,
                "Authorization": f"Bearer {token}",
            },
        )
    if response.status_code != 200:
        return None
    try:
        data = response.json()
    except ValueError:
        return None

    jwt_payload = decode_jwt_payload(token)
    return {
        "id": data.get("id") or jwt_payload.get("sub"),
        "email": data.get("email") or jwt_payload.get("email"),
        "raw": data,
    }


async def supabase_rest_request(
    method: str,
    path: str,
    *,
    params: dict | None = None,
    payload: dict | list | None = None,
    prefer: str | None = None,
):
    if not supabase_rest_configured():
        return 503, {"error": "Supabase database belum dikonfigurasi di environment."}, {}

    headers = supabase_headers(SUPABASE_SERVICE_ROLE_KEY)
    if prefer:
        headers["Prefer"] = prefer

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.request(
            method,
            f"{SUPABASE_URL}{path}",
            headers=headers,
            params=params,
            json=payload,
        )
    try:
        data = response.json()
    except ValueError:
        data = {"error": response.text}
    return response.status_code, data, response.headers


def parse_epoch_millis(value) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None
    if timestamp > 10_000_000_000:
        timestamp = timestamp / 1000
    try:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None


async def fetch_user_profile(user_id: str) -> dict | None:
  status_code, data, _ = await supabase_rest_request(
    "GET",
    "/rest/v1/profiles",
    params={
      "select": "id,email,tier,daily_searches_left,last_search_reset,monthly_profiles_used,monthly_comments_used,monthly_reset_date",
      "id": f"eq.{user_id}",
      "limit": "1",
    },
  )
  if status_code != 200:
    status_code, data, _ = await supabase_rest_request(
      "GET",
      "/rest/v1/profiles",
      params={
        "select": "id,email,tier,daily_searches_left,last_search_reset",
        "id": f"eq.{user_id}",
        "limit": "1",
      },
    )
  if status_code != 200 or not isinstance(data, list) or not data:
    return None
  return data[0]

async def get_and_reset_profile_usage(user_id: str) -> dict | None:
    # ── Owner always gets unlimited Pro profile ──
    if user_id == OWNER_USER_ID:
        return {
            "id": OWNER_USER_ID,
            "email": OWNER_EMAIL,
            "tier": "pro",
            "daily_searches_left": 999_999,
            "monthly_profiles_used": 0,
            "monthly_comments_used": 0,
            "allowed_platforms": ["tiktok", "instagram", "youtube", "twitter", "facebook"],
        }

    profile = await fetch_user_profile(user_id)
    if not profile:
        return None

    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    this_month = now.strftime("%Y-%m")
    last_reset = profile.get("last_search_reset", "")
    last_monthly_reset = profile.get("monthly_reset_date", "") or ""

    patch_payload: dict = {}

    # Daily reset
    if not last_reset.startswith(today):
        tier = profile.get("tier", "free")
        plan = PLAN_CATALOG.get(tier) or PLAN_CATALOG["free"]
        new_limit = plan["daily_search_limit"]
        patch_payload["daily_searches_left"] = new_limit
        patch_payload["last_search_reset"] = now.isoformat()
        profile["daily_searches_left"] = new_limit
        profile["last_search_reset"] = now.isoformat()

    # Monthly reset — clear usage counters when month rolls over
    if not last_monthly_reset.startswith(this_month):
        patch_payload["monthly_profiles_used"] = 0
        patch_payload["monthly_comments_used"] = 0
        patch_payload["monthly_reset_date"] = now.strftime("%Y-%m-01")
        profile["monthly_profiles_used"] = 0
        profile["monthly_comments_used"] = 0
        profile["monthly_reset_date"] = now.strftime("%Y-%m-01")
        # Also clear in-memory counters for this user
        for feature in ("profile", "comments", "transcript", "search"):
            MONTHLY_USAGE_COUNTERS.pop((user_id, feature, _current_month_key()), None)

    if patch_payload:
        await supabase_rest_request(
            "PATCH",
            f"/rest/v1/profiles?id=eq.{user_id}",
            payload=patch_payload,
        )

    return profile

async def decrement_search_limit(user_id: str, current_limit: int):
    new_limit = max(0, current_limit - 1)
    await supabase_rest_request(
        "PATCH",
        f"/rest/v1/profiles?id=eq.{user_id}",
        payload={"daily_searches_left": new_limit}
    )



def get_client_ip(request: Request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    real_ip = (request.headers.get("x-real-ip") or "").strip()
    if forwarded:
        return forwarded
    if real_ip:
        return real_ip
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def enforce_rate_limit(request: Request, bucket: str) -> JSONResponse | None:
    rule = RATE_LIMIT_RULES.get(bucket)
    if not rule:
        return None
    limit, window_seconds = rule
    now = time.time()
    key = f"{bucket}:{get_client_ip(request)}"
    timestamps = [ts for ts in RATE_LIMIT_BUCKETS.get(key, []) if now - ts < window_seconds]
    if len(timestamps) >= limit:
        retry_after = max(1, int(window_seconds - (now - timestamps[0])))
        response = JSONResponse(
            {
                "error": "Terlalu banyak request. Coba lagi sebentar lagi.",
                "code": "rate_limited",
                "bucket": bucket,
                "retry_after": retry_after,
            },
            status_code=429,
        )
        response.headers["Retry-After"] = str(retry_after)
        return response
    timestamps.append(now)
    RATE_LIMIT_BUCKETS[key] = timestamps
    return None


def _get_free_plan() -> dict:
    return {**PLAN_CATALOG["free"], "code": "free"}


def _check_ip_daily_search(request: Request) -> tuple[int, int]:
    ip = get_client_ip(request)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    entry = IP_DAILY_SEARCH_COUNTS.get(ip)
    if entry and entry[0] == today:
        return entry[1], FREE_DAILY_SEARCH_LIMIT
    return 0, FREE_DAILY_SEARCH_LIMIT


def _increment_ip_daily_search(request: Request):
    ip = get_client_ip(request)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    entry = IP_DAILY_SEARCH_COUNTS.get(ip)
    if entry and entry[0] == today:
        IP_DAILY_SEARCH_COUNTS[ip] = (today, entry[1] + 1)
    else:
        IP_DAILY_SEARCH_COUNTS[ip] = (today, 1)


async def enforce_feature_access(request: Request, feature: str) -> tuple[dict | None, dict | None, JSONResponse | None]:
  if DEV_AUTH_BYPASS:
    dev_plan = {**PLAN_CATALOG["pro"], "code": "pro"}
    dev_user = {
      "id": "dev-local-user",
      "email": "dev@local",
      "profile": {
        "tier": "pro",
        "daily_searches_left": 999_999,
      },
    }
    return dev_user, dev_plan, None

  # ── Owner bypass ──
  token = request.cookies.get(AUTH_COOKIE_NAME)
  if token and _is_owner_token(token):
    owner_plan = {**PLAN_CATALOG["pro"], "code": "pro"}
    owner_user = {"id": OWNER_USER_ID, "email": OWNER_EMAIL}
    return owner_user, owner_plan, None

  if not supabase_auth_configured():
    return None, None, None

  user = await get_authenticated_user(request)

  if not user:
    if feature == "search":
      used, limit = _check_ip_daily_search(request)
      if used >= limit:
        return None, _get_free_plan(), JSONResponse(
          {
            "error": "Kuota pencarian gratis hari ini habis (3/hari). Daftar untuk akses lebih.",
            "code": "free_quota_exceeded",
            "feature": feature,
            "limit": limit,
            "used": used,
            "plan_code": "free",
            "upgrade_url": "/signup",
          },
          status_code=429,
        )
      return None, _get_free_plan(), None
    return None, None, JSONResponse(
      {"error": "Silakan login dulu untuk memakai fitur ini.", "code": "auth_required"},
      status_code=401,
    )

  if not supabase_rest_configured():
    return user, None, None

  profile = await get_and_reset_profile_usage(user["id"])
  if not profile:
    profile = {"tier": "free", "daily_searches_left": FREE_DAILY_SEARCH_LIMIT}

  tier = profile.get("tier", "free")
  plan = PLAN_CATALOG.get(tier) or _get_free_plan()
  plan["code"] = tier
  user["profile"] = profile

  if feature == "search":
    daily_limit = plan.get("daily_search_limit", 0)
    daily_searches_left = int(profile.get("daily_searches_left", 0))
    if daily_limit > 0 and daily_searches_left <= 0:
      return user, plan, JSONResponse(
        {
          "error": "Kuota pencarian hari ini habis. Upgrade untuk lebih banyak.",
          "code": "quota_exceeded",
          "feature": feature,
          "limit": daily_limit,
          "used": daily_limit,
          "plan_code": plan.get("code"),
          "upgrade_url": plan.get("checkout_url") or "/payment",
        },
        status_code=429,
      )
    return user, plan, None

  if tier == "free":
    return user, plan, JSONResponse(
      {"error": "Fitur ini khusus akun Starter / Pro. Silakan upgrade.", "code": "upgrade_required", "upgrade_url": "/payment"},
      status_code=403,
    )

  return user, plan, None


def mayar_secret_matches(request: Request) -> bool:
    expected = os.getenv("MAYAR_WEBHOOK_SECRET", "").strip()
    if not expected:
        print("[WARN] MAYAR_WEBHOOK_SECRET not configured — rejecting webhook")
        return False
    candidates = [
        request.headers.get("x-webhook-secret", ""),
        request.headers.get("x-mayar-webhook-secret", ""),
    ]
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        candidates.append(auth_header.split(" ", 1)[1].strip())
    return any(candidate == expected for candidate in candidates)


async def upsert_payment_transaction(payment: dict) -> dict | None:
    """Insert or update a payment transaction record.

    Idempotent: if a row with the same (provider, provider_invoice_id) already
    exists it is updated in-place, preventing duplicate rows on webhook retries.
    """
    provider_invoice_id = payment.get("provider_invoice_id")
    provider = payment.get("provider", "mayar")
    existing = None

    if provider_invoice_id:
        status_code, data, _ = await supabase_rest_request(
            "GET",
            "/rest/v1/payment_transactions",
            params={
                "select": "*",
                "provider": f"eq.{provider}",
                "provider_invoice_id": f"eq.{provider_invoice_id}",
                "limit": "1",
            },
        )
        if status_code == 200 and isinstance(data, list) and data:
            existing = data[0]

    method = "PATCH" if existing else "POST"
    path = "/rest/v1/payment_transactions"
    params = {"id": f"eq.{existing['id']}"} if existing else None
    status_code, data, _ = await supabase_rest_request(
        method,
        path,
        params=params,
        payload=payment,
        prefer="return=representation",
    )
    if status_code not in (200, 201):
        return existing
    return data[0] if isinstance(data, list) and data else existing


async def upsert_subscription_record(
    *,
    user_id: str,
    plan_code: str,
    provider_invoice_id: str | None,
    status: str,
    paid_at: datetime | None,
):
    existing = await fetch_latest_subscription(user_id)
    start_at = paid_at or datetime.now(timezone.utc)
    end_at = billing_period_end(start_at, plan_code)
    payload = {
        "user_id": user_id,
        "plan_code": plan_code,
        "provider": "mayar",
        "provider_invoice_id": provider_invoice_id,
        "status": status,
        "current_period_start": start_at.isoformat() if status == "active" else None,
        "current_period_end": end_at.isoformat() if status == "active" and end_at else None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    method = "PATCH" if existing else "POST"
    params = {"id": f"eq.{existing['id']}"} if existing else None
    status_code, data, _ = await supabase_rest_request(
        method,
        "/rest/v1/subscriptions",
        params=params,
        payload=payload,
        prefer="return=representation",
    )
    if status_code not in (200, 201):
        return existing
    return data[0] if isinstance(data, list) and data else existing


def render_public_account_page(
    *,
    title: str,
    eyebrow: str = "",
    heading: str,
    subheading: str,
    primary_label: str,
    secondary_label: str,
    secondary_href: str,
    form_fields: str,
    aside_title: str = "",
    aside_body: str = "",
    aside_list: list[str] | None = None,
    footer_note: str = "",
    extra_script: str = "",
    show_google: bool = False,
    mode: str = "signup",
):
    google_section = ""
    if show_google:
        google_section = f"""
      <button class="auth-social" type="button" onclick="googleAuth()">
        <svg width="18" height="18" viewBox="0 0 24 24"><path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 01-2.2 3.32v2.77h3.57c2.08-1.92 3.27-4.74 3.27-8.1z" fill="#4285F4"/><path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/><path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/><path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/></svg>
        <span>Lanjut dengan Google</span>
      </button>
      <div class="auth-divider"><span>atau</span></div>
      """

    return f"""<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=DM+Serif+Display:ital@0;1&display=swap" rel="stylesheet">
<style>
:root {{
  --bg: #faf3ec;
  --ink: #3b1a08;
  --soft: #705b4c;
  --muted: #9a8474;
  --line: rgba(84,52,29,0.08);
  --card: #fff;
  --accent: #c0391b;
  --accent-2: #ef5a29;
  --green: #285f58;
}}
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:'Plus Jakarta Sans',sans-serif;color:var(--ink);background:var(--bg);-webkit-font-smoothing:antialiased;min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;}}
a{{text-decoration:none;color:inherit;}}

.auth-page{{width:100%;min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:24px;}}
.auth-card{{width:100%;max-width:420px;background:var(--card);border:1px solid var(--line);border-radius:20px;box-shadow:0 12px 48px rgba(98,66,43,0.08);padding:36px 32px;}}
.auth-logo{{text-align:center;margin-bottom:24px;}}
.auth-logo a{{font-family:'DM Serif Display',serif;font-size:32px;letter-spacing:-0.04em;color:var(--ink);}}
.auth-logo a span{{color:var(--accent);}}
.auth-title{{text-align:center;font-family:'DM Serif Display',serif;font-size:24px;letter-spacing:-0.02em;margin-bottom:6px;}}
.auth-sub{{text-align:center;color:var(--soft);font-size:14px;line-height:1.6;margin-bottom:24px;}}

.auth-social{{display:flex;align-items:center;justify-content:center;gap:10px;width:100%;padding:12px 16px;border-radius:12px;border:1.5px solid var(--line);background:rgba(255,255,255,0.8);cursor:pointer;font:inherit;font-size:14px;font-weight:700;color:var(--ink);transition:border-color .15s,box-shadow .15s;}}
.auth-social:hover{{border-color:rgba(84,52,29,0.2);box-shadow:0 4px 12px rgba(98,66,43,0.06);}}
.auth-divider{{display:flex;align-items:center;gap:12px;margin:20px 0;}}
.auth-divider::before,.auth-divider::after{{content:"";flex:1;height:1px;background:var(--line);}}
.auth-divider span{{font-size:12px;color:var(--muted);font-weight:600;text-transform:lowercase;}}

.field{{display:grid;gap:5px;margin-bottom:14px;}}
.field label{{font-size:13px;font-weight:700;color:var(--ink);}}
.field input,.field select{{width:100%;padding:12px 14px;border-radius:12px;border:1.5px solid var(--line);background:rgba(255,255,255,0.7);font:inherit;font-size:16px;color:var(--ink);outline:none;transition:border-color .15s;}}
.field input:focus,.field select:focus{{border-color:var(--accent);}}
.field input::placeholder{{color:var(--muted);}}

.submit{{border:0;cursor:pointer;width:100%;padding:14px;border-radius:12px;background:linear-gradient(135deg,var(--accent),var(--accent-2));color:#fff;font:inherit;font-weight:800;font-size:15px;box-shadow:0 8px 24px rgba(192,57,27,0.2);transition:transform .15s,box-shadow .15s;margin-top:6px;}}
.submit:hover{{transform:translateY(-1px);box-shadow:0 12px 32px rgba(192,57,27,0.3);}}

.auth-footer{{text-align:center;margin-top:20px;font-size:13px;color:var(--soft);}}
.auth-footer a{{color:var(--accent);font-weight:700;}}
.auth-footer a:hover{{text-decoration:underline;}}

.note{{padding:12px 14px;border-radius:12px;background:rgba(40,95,88,0.08);color:var(--green);font-size:13px;line-height:1.6;margin-top:12px;text-align:center;}}

.page-footer{{margin-top:24px;text-align:center;}}
.page-footer a{{font-size:13px;color:var(--muted);font-weight:600;padding:0 10px;transition:color .15s;}}
.page-footer a:hover{{color:var(--accent);}}

@media(max-width:480px){{
  .auth-card{{padding:28px 20px;border-radius:16px;}}
}}
</style>
</head>
<body>
<div class="auth-page">
  <div class="auth-card">
    <div class="auth-logo"><a href="/">Sin<span>yal</span></a></div>
    <h1 class="auth-title">{primary_label}</h1>
    <p class="auth-sub">{subheading}</p>

    {google_section}

    <form id="authForm" onsubmit="return false;">
    {form_fields}
    <button class="submit" type="submit">{primary_label}</button>
    </form>

    <div class="auth-footer">
      <a href="{secondary_href}">{secondary_label}</a>
    </div>
  </div>

  <div class="page-footer">
    <a href="/">Beranda</a>
    <a href="/payment">Harga</a>
    <a href="/affiliate">Affiliate</a>
  </div>
</div>

<script>
function googleAuth() {{
  window.location.href = '/api/auth/google';
}}
</script>
{extra_script}
</body>
</html>"""


def render_payment_page():
    plan_data = []
    for plan in get_plan_catalog():
        yearly_price = 0
        yearly_label = ""
        monthly_price = plan["price_idr"]
        if plan["code"] == "starter":
            yearly_price = 389_000
            yearly_label = "Rp389rb"
        elif plan["code"] == "pro":
            yearly_price = 789_000
            yearly_label = "Rp789rb"
        
        plan_data.append({
            **plan,
            "yearly_price": yearly_price,
            "yearly_label": yearly_label,
        })
    
    return f"""<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Harga - Sinyal</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=DM+Serif+Display:ital@0;1&display=swap" rel="stylesheet">
<link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&display=swap" rel="stylesheet">
<style>
:root {{
  --bg: #f8f0e4;
  --bg-soft: #fff9f2;
  --ink: #20160f;
  --soft: #705b4c;
  --muted: #9a8474;
  --line: rgba(84,52,29,0.10);
  --card: rgba(255,250,244,0.9);
  --card-strong: rgba(255,255,255,0.92);
  --accent: #ef5a29;
  --accent-2: #ff8d42;
  --accent-soft: rgba(239,90,41,0.10);
  --green: #285f58;
  --green-soft: rgba(40,95,88,0.10);
  --radius: 20px;
  --shadow-sm: 0 2px 8px rgba(98,66,43,0.06);
  --shadow-md: 0 12px 40px rgba(98,66,43,0.08);
  --shadow-lg: 0 24px 64px rgba(98,66,43,0.10);
}}
*{{box-sizing:border-box;margin:0;padding:0;}}
html{{scroll-behavior:smooth;}}
body{{font-family:'Plus Jakarta Sans',sans-serif;color:var(--ink);background:linear-gradient(180deg,#fffaf4 0%,#f8f0e4 60%,#f1e5d8 100%);-webkit-font-smoothing:antialiased;}}
a{{text-decoration:none;color:inherit;}}
.wrap{{width:min(1120px,100% - 40px);margin:0 auto;}}
.material-symbols-outlined{{font-variation-settings:'FILL' 0,'wght' 400;vertical-align:middle;}}

/* ── Nav ── */
nav{{position:sticky;top:0;z-index:50;background:rgba(255,250,244,0.82);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);border-bottom:1px solid var(--line);}}
.nav-inner{{display:flex;align-items:center;justify-content:space-between;height:68px;gap:16px;}}
.brand{{font-family:'DM Serif Display',serif;font-size:28px;letter-spacing:-0.04em;}}
.brand em{{color:var(--accent);font-style:normal;}}
.nav-links{{display:flex;align-items:center;gap:8px;}}
.nav-links a{{font-size:14px;font-weight:600;color:var(--soft);padding:8px 14px;border-radius:10px;transition:all .15s;}}
.nav-links a:hover{{background:rgba(239,90,41,0.06);color:var(--accent);}}
.btn{{display:inline-flex;align-items:center;justify-content:center;gap:8px;padding:12px 22px;border-radius:12px;font-weight:800;font-size:14px;border:none;cursor:pointer;transition:transform .15s,box-shadow .15s;}}
.btn:hover{{transform:translateY(-1px);}}
.btn-primary{{background:linear-gradient(135deg,var(--accent),var(--accent-2));color:#fff;box-shadow:0 8px 24px rgba(239,90,41,0.22);}}
.btn-ghost{{background:rgba(255,255,255,0.7);border:1.5px solid var(--line);color:var(--ink);}}

/* ── Page header ── */
.page-header{{text-align:center;padding:56px 0 12px;}}
.page-header h1{{font-family:'DM Serif Display',serif;font-size:clamp(32px,5vw,52px);line-height:1.05;letter-spacing:-0.04em;margin-bottom:12px;}}
.page-header p{{color:var(--soft);font-size:16px;line-height:1.7;max-width:560px;margin:0 auto;}}

/* ── Toggle ── */
.billing-toggle-wrap{{display:flex;justify-content:center;margin:28px 0 36px;}}
.billing-toggle{{display:flex;align-items:center;background:var(--card-strong);border:1.5px solid var(--line);border-radius:999px;padding:5px;gap:4px;box-shadow:var(--shadow-sm);}}
.billing-btn{{padding:10px 24px;border-radius:999px;border:none;background:transparent;font-family:inherit;font-size:14px;font-weight:700;color:var(--muted);cursor:pointer;transition:all .2s;display:flex;align-items:center;gap:8px;}}
.billing-btn.active{{background:var(--ink);color:#fff;box-shadow:0 4px 12px rgba(0,0,0,0.12);}}
.save-badge{{padding:3px 10px;border-radius:999px;background:#16a34a;color:#fff;font-size:11px;font-weight:800;letter-spacing:0.02em;}}

/* ── Pricing grid ── */
.pricing-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:32px;}}
.price-card{{padding:28px 24px;border-radius:22px;background:var(--card-strong);border:1.5px solid var(--line);display:flex;flex-direction:column;transition:transform .25s,box-shadow .25s;position:relative;}}
.price-card:hover{{transform:translateY(-4px);box-shadow:var(--shadow-lg);}}
.price-card.featured{{border-color:rgba(239,90,41,0.25);box-shadow:0 8px 32px rgba(239,90,41,0.08);}}
.popular-badge{{position:absolute;top:-12px;left:24px;padding:6px 14px;border-radius:999px;background:linear-gradient(135deg,var(--accent),var(--accent-2));color:#fff;font-size:11px;font-weight:800;letter-spacing:0.02em;}}
.price-card h3{{font-size:22px;font-weight:800;margin-bottom:8px;}}
.price-card .tagline{{color:var(--soft);font-size:13px;line-height:1.55;margin-bottom:18px;min-height:40px;}}

/* Price display */
.price-amount{{margin-bottom:6px;}}
.price-amount .main{{font-family:'DM Serif Display',serif;font-size:42px;letter-spacing:-0.04em;line-height:1;}}
.price-amount .period{{font-size:15px;color:var(--muted);font-weight:600;}}
.price-annual-note{{font-size:13px;color:var(--soft);margin-bottom:18px;height:20px;}}

/* CTA */
.price-cta{{margin-top:auto;padding-top:22px;}}
.price-cta a{{display:flex;align-items:center;justify-content:center;width:100%;padding:14px;border-radius:14px;font-weight:800;font-size:14px;transition:transform .15s,box-shadow .15s;}}
.price-cta a:hover{{transform:translateY(-1px);}}
.price-cta-primary a{{color:#fff;background:linear-gradient(135deg,var(--accent),var(--accent-2));box-shadow:0 8px 24px rgba(239,90,41,0.2);}}
.price-cta-primary a:hover{{box-shadow:0 12px 32px rgba(239,90,41,0.3);}}
.price-cta-ghost a{{color:var(--ink);background:rgba(255,255,255,0.8);border:1.5px solid var(--line);}}
.price-cta-ghost a:hover{{border-color:var(--accent);color:var(--accent);}}

/* Features section */
.feat-section-label{{display:flex;align-items:center;gap:8px;font-size:12px;font-weight:800;color:var(--muted);text-transform:uppercase;letter-spacing:0.06em;margin-bottom:14px;padding-bottom:10px;border-bottom:1px solid var(--line);}}
.feat-section-label .material-symbols-outlined{{font-size:16px;color:var(--accent);}}
.feat-row{{display:flex;align-items:flex-start;gap:10px;padding:7px 0;font-size:13px;color:var(--soft);line-height:1.5;}}
.feat-row .material-symbols-outlined{{font-size:18px;color:var(--accent);flex-shrink:0;margin-top:1px;}}
.feat-row.disabled{{opacity:0.4;}}
.feat-row.disabled .material-symbols-outlined{{color:var(--muted);}}
.feat-highlight{{display:inline-flex;padding:2px 10px;border-radius:999px;font-size:11px;font-weight:800;margin-left:auto;flex-shrink:0;}}
.feat-highlight.green{{background:rgba(22,163,74,0.1);color:#16a34a;}}
.feat-highlight.orange{{background:rgba(239,90,41,0.1);color:var(--accent);}}
.feat-highlight.blue{{background:rgba(59,130,246,0.08);color:#3b82f6;}}

/* Compare link */
.compare-link{{font-size:13px;color:var(--accent);font-weight:700;cursor:pointer;display:inline-flex;align-items:center;gap:4px;margin-top:auto;padding-top:16px;border:none;background:none;font-family:inherit;}}
.compare-link:hover{{text-decoration:underline;}}

/* FAQ */
.faq-section{{margin-bottom:48px;}}
.faq-header{{text-align:center;margin-bottom:28px;}}
.faq-header h2{{font-family:'DM Serif Display',serif;font-size:28px;letter-spacing:-0.03em;}}
.faq-grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px;}}
.faq-card{{padding:20px 24px;border-radius:16px;background:var(--card-strong);border:1px solid var(--line);}}
.faq-card strong{{display:block;font-size:14px;font-weight:800;margin-bottom:6px;}}
.faq-card p{{color:var(--soft);font-size:13px;line-height:1.65;}}

/* Footer */
.footnote{{text-align:center;padding:24px;color:var(--muted);font-size:13px;line-height:1.7;}}
.footnote a{{color:var(--accent);font-weight:700;}}

/* Responsive */
@media(max-width:1100px){{.pricing-grid{{grid-template-columns:repeat(2,1fr);}}}}
@media(max-width:720px){{
  .pricing-grid{{grid-template-columns:1fr;}}
  .faq-grid{{grid-template-columns:1fr;}}
  .billing-btn{{padding:8px 16px;font-size:13px;}}
  .nav-links{{display:none;position:absolute;top:64px;left:0;right:0;flex-direction:column;background:rgba(255,250,244,0.98);backdrop-filter:blur(16px);padding:20px;border-bottom:1px solid var(--line);gap:8px;z-index:99;}}
  .nav-links.open{{display:flex;}}
  .hamburger{{display:flex !important;}}
}}
.hamburger{{display:none;background:none;border:none;cursor:pointer;padding:8px;flex-direction:column;gap:5px;}}
.hamburger span{{display:block;width:22px;height:2px;background:var(--ink);border-radius:2px;}}
</style>
</head>
<body>

<nav>
  <div class="wrap nav-inner">
    <a href="/" class="brand">Sin<em>yal</em></a>
    <button class="hamburger" onclick="document.querySelector('.nav-links').classList.toggle('open')" aria-label="Menu"><span></span><span></span><span></span></button>
    <div class="nav-links">
      <a href="/">Beranda</a>
      <a href="/app">App</a>
      <a href="/signin">Masuk</a>
      <a href="/signup" class="btn btn-primary" style="padding:10px 18px;font-size:13px;">Coba Gratis</a>
    </div>
  </div>
</nav>

<div class="wrap">
  <div class="page-header">
    <h1>Harga yang masih masuk akal</h1>
    <p>Mulai gratis, upgrade kalau butuh lebih. Bayar bulanan atau hemat dengan tahunan.</p>
  </div>

  <!-- Billing Toggle -->
  <div class="billing-toggle-wrap">
    <div class="billing-toggle">
      <button class="billing-btn active" data-billing="monthly" type="button">Bulanan</button>
      <button class="billing-btn" data-billing="yearly" type="button">Tahunan <span class="save-badge">Hemat ~35%</span></button>
    </div>
  </div>

  <!-- Pricing Cards -->
  <div class="pricing-grid">

    <!-- Free -->
    <div class="price-card">
      <h3>Free</h3>
      <div class="tagline">Mulai riset konten viral tanpa biaya.</div>
      <div class="price-amount">
        <span class="main">Rp0</span>
        <span class="period">/ selamanya</span>
      </div>
      <div class="price-annual-note">&nbsp;</div>
      <div class="feat-section-label"><span class="material-symbols-outlined">checklist</span>Main Features</div>
      <div class="feat-row"><span class="material-symbols-outlined">search</span>3 pencarian per hari</div>
      <div class="feat-row"><span class="material-symbols-outlined">play_circle</span>TikTok saja</div>
      <div class="feat-row"><span class="material-symbols-outlined">trending_up</span>Daily Briefing (preview)</div>
      <div class="feat-row"><span class="material-symbols-outlined">biotech</span>1 Content Autopsy / minggu</div>
      <div class="feat-row"><span class="material-symbols-outlined">download</span>Export dengan watermark</div>
      <div class="feat-row disabled"><span class="material-symbols-outlined">person_search</span>Profil creator</div>
      <div class="feat-row disabled"><span class="material-symbols-outlined">comment</span>Tarik komentar</div>
      <div class="price-cta price-cta-ghost"><a href="/signup">Mulai Gratis</a></div>
    </div>

    <!-- Paket 7 Hari -->
    <div class="price-card" style="border-color:rgba(40,95,88,0.2);">
      <div class="popular-badge" style="background:linear-gradient(135deg,#285f58,#3a8f85);">Paling Laris</div>
      <h3>Paket 7 Hari</h3>
      <div class="tagline">Akses penuh selama 7 hari. Tanpa auto-renew.</div>
      <div class="price-amount">
        <span class="main">Rp29rb</span>
        <span class="period">/ 7 hari</span>
      </div>
      <div class="price-annual-note"><span style="color:var(--green);font-weight:700;">Sekali bayar, langsung aktif</span></div>
      <div class="feat-section-label"><span class="material-symbols-outlined">checklist</span>Main Features</div>
      <div class="feat-row"><span class="material-symbols-outlined">all_inclusive</span>Unlimited pencarian 7 hari</div>
      <div class="feat-row"><span class="material-symbols-outlined">play_circle</span>TikTok + IG + YouTube<span class="feat-highlight green">3 platform</span></div>
      <div class="feat-row"><span class="material-symbols-outlined">person_search</span>10 cek profil</div>
      <div class="feat-row"><span class="material-symbols-outlined">comment</span>10 tarik komentar</div>
      <div class="feat-row"><span class="material-symbols-outlined">auto_awesome</span>10 analisa konten AI</div>
      <div class="feat-row"><span class="material-symbols-outlined">download</span>Export tanpa watermark</div>
      <div class="price-cta price-cta-primary" style="background:linear-gradient(135deg,#285f58,#3a8f85) !important;"><a href="/checkout/weekly" style="color:#fff;">Beli Paket 7 Hari</a></div>
    </div>

    <!-- Paket 50 Kredit -->
    <div class="price-card">
      <h3>Paket 50 Kredit</h3>
      <div class="tagline">Beli sekali, pakai kapan saja. Tidak hangus.</div>
      <div class="price-amount">
        <span class="main">Rp49rb</span>
        <span class="period">/ 50 kredit</span>
      </div>
      <div class="price-annual-note"><span style="color:#2563eb;font-weight:700;">1 search = 1 kredit</span></div>
      <div class="feat-section-label"><span class="material-symbols-outlined">checklist</span>Main Features</div>
      <div class="feat-row"><span class="material-symbols-outlined">token</span>50 kredit fleksibel</div>
      <div class="feat-row"><span class="material-symbols-outlined">play_circle</span>TikTok + IG + YouTube<span class="feat-highlight green">3 platform</span></div>
      <div class="feat-row"><span class="material-symbols-outlined">person_search</span>Profil &amp; komentar 1 kredit</div>
      <div class="feat-row"><span class="material-symbols-outlined">auto_awesome</span>Analisa konten AI termasuk</div>
      <div class="feat-row"><span class="material-symbols-outlined">download</span>Export tanpa watermark</div>
      <div class="feat-row"><span class="material-symbols-outlined">schedule</span>Tidak hangus — pakai kapan saja</div>
      <div class="price-cta price-cta-ghost"><a href="/checkout/credit">Beli 50 Kredit</a></div>
    </div>

    <!-- Starter -->
    <div class="price-card">
      <h3>Starter</h3>
      <div class="tagline">Untuk solopreneur yang mulai serius riset konten.</div>
      <div class="price-amount">
        <span class="main" data-monthly="Rp49rb" data-yearly="Rp32rb">Rp49rb</span>
        <span class="period" data-monthly="/ bulan" data-yearly="/ bulan">/ bulan</span>
      </div>
      <div class="price-annual-note" data-monthly="&nbsp;" data-yearly="Rp389rb / tahun">&nbsp;</div>
      <div class="feat-section-label"><span class="material-symbols-outlined">checklist</span>Main Features</div>
      <div class="feat-row"><span class="material-symbols-outlined">search</span>30 pencarian per hari</div>
      <div class="feat-row"><span class="material-symbols-outlined">play_circle</span>TikTok + Instagram<span class="feat-highlight green">2 platform</span></div>
      <div class="feat-row"><span class="material-symbols-outlined">trending_up</span>Full Daily Briefing</div>
      <div class="feat-row"><span class="material-symbols-outlined">auto_awesome</span>100 insight / bulan</div>
      <div class="feat-row"><span class="material-symbols-outlined">biotech</span>10 Content Autopsy / bulan</div>
      <div class="feat-row"><span class="material-symbols-outlined">menu_book</span>1 Niche Playbook</div>
      <div class="feat-row"><span class="material-symbols-outlined">person_search</span>20 cek profil / bulan</div>
      <div class="feat-row"><span class="material-symbols-outlined">comment</span>20 tarik komentar / bulan</div>
      <div class="feat-row"><span class="material-symbols-outlined">download</span>Export tanpa watermark</div>
      <div class="price-cta price-cta-ghost"><a href="/checkout/starter">Ambil Starter</a></div>
    </div>

    <!-- Pro (featured) -->
    <div class="price-card featured">
      <div class="popular-badge">Most Popular</div>
      <h3>Pro</h3>
      <div class="tagline">Untuk brand & tim yang butuh insight cepat dari semua platform.</div>
      <div class="price-amount">
        <span class="main" data-monthly="Rp99rb" data-yearly="Rp65rb">Rp99rb</span>
        <span class="period" data-monthly="/ bulan" data-yearly="/ bulan">/ bulan</span>
      </div>
      <div class="price-annual-note" data-monthly="&nbsp;" data-yearly="Rp789rb / tahun">&nbsp;</div>
      <div class="feat-section-label"><span class="material-symbols-outlined">checklist</span>Main Features</div>
      <div class="feat-row"><span class="material-symbols-outlined">all_inclusive</span>Pencarian unlimited</div>
      <div class="feat-row"><span class="material-symbols-outlined">play_circle</span>Semua platform<span class="feat-highlight orange">5 platform</span></div>
      <div class="feat-row"><span class="material-symbols-outlined">trending_up</span>Full Daily Briefing</div>
      <div class="feat-row"><span class="material-symbols-outlined">biotech</span>Unlimited Content Autopsy</div>
      <div class="feat-row"><span class="material-symbols-outlined">menu_book</span>Semua Niche Playbook</div>
      <div class="feat-row"><span class="material-symbols-outlined">person_search</span>Unlimited profil creator</div>
      <div class="feat-row"><span class="material-symbols-outlined">comment</span>Unlimited komentar</div>
      <div class="feat-row"><span class="material-symbols-outlined">auto_awesome</span>Unlimited analisa konten AI</div>
      <div class="feat-row"><span class="material-symbols-outlined">bolt</span>Hook, CTA &amp; angle analysis</div>
      <div class="feat-row"><span class="material-symbols-outlined">download</span>Export tanpa watermark</div>
      <div class="price-cta price-cta-primary"><a href="/checkout/pro">Upgrade ke Pro</a></div>
    </div>

  </div>

  <!-- FAQ -->
  <div class="faq-section">
    <div class="faq-header"><h2>Pertanyaan yang sering muncul</h2></div>
    <div class="faq-grid">
      <div class="faq-card">
        <strong>Bisa ganti paket kapan saja?</strong>
        <p>Bisa. Upgrade atau downgrade langsung dari halaman akun. Perubahan berlaku segera.</p>
      </div>
      <div class="faq-card">
        <strong>Apa bedanya yearly vs monthly?</strong>
        <p>Fitur sama persis. Yearly lebih hemat sekitar 35% karena bayar di muka untuk setahun.</p>
      </div>
      <div class="faq-card">
        <strong>Apa itu Content Autopsy?</strong>
        <p>Paste link video TikTok kamu, Sinyal diagnosa kenapa perform atau flop — lengkap dengan rekomendasi perbaikan.</p>
      </div>
      <div class="faq-card">
        <strong>Bisa pakai buat tim?</strong>
        <p>Saat ini 1 akun = 1 user. Fitur tim/seat sedang dikembangkan dan akan hadir di update mendatang.</p>
      </div>
      <div class="faq-card">
        <strong>Pembayaran pakai apa?</strong>
        <p>Transfer bank, QRIS, e-wallet, dan kartu kredit. Semua diproses aman lewat Mayar.</p>
      </div>
      <div class="faq-card">
        <strong>Kalau mau refund?</strong>
        <p>Hubungi kami dalam 7 hari pertama. Kami proses refund penuh tanpa ribet.</p>
      </div>
    </div>
  </div>

  <div class="footnote">
    Punya pertanyaan lain? <a href="/signin">Masuk</a> atau <a href="/">kembali ke beranda</a>.
  </div>
</div>

<script>
/* Billing toggle */
document.querySelectorAll('.billing-btn').forEach(function(btn) {{
  btn.addEventListener('click', function() {{
    document.querySelectorAll('.billing-btn').forEach(function(b) {{ b.classList.remove('active'); }});
    btn.classList.add('active');
    var billing = btn.dataset.billing;
    
    document.querySelectorAll('.price-amount .main[data-monthly]').forEach(function(el) {{
      el.textContent = el.dataset[billing];
    }});
    document.querySelectorAll('.price-amount .period[data-monthly]').forEach(function(el) {{
      el.textContent = el.dataset[billing];
    }});
    document.querySelectorAll('.price-annual-note[data-monthly]').forEach(function(el) {{
      el.innerHTML = el.dataset[billing];
    }});
  }});
}});
</script>
</body>
</html>"""


def render_start_page():
    return """<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Lanjutkan Setup - Sinyal</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=DM+Serif+Display:ital@0;1&display=swap" rel="stylesheet">
<style>
:root {
  --bg: #faf3ec;
  --ink: #3b1a08;
  --soft: #705b4c;
  --muted: #9a8474;
  --line: rgba(84,52,29,0.08);
  --card: rgba(255,250,244,0.9);
  --accent: #c0391b;
  --accent-2: #ef5a29;
  --green: #285f58;
}
*{box-sizing:border-box;margin:0;padding:0;}
body{font-family:'Plus Jakarta Sans',sans-serif;color:var(--ink);background:var(--bg);-webkit-font-smoothing:antialiased;min-height:100vh;display:grid;place-items:center;padding:24px;}
a{text-decoration:none;color:inherit;}
.shell{width:min(720px,100%);display:grid;gap:18px;}
.brand{font-family:'DM Serif Display',serif;font-size:28px;letter-spacing:-0.03em;color:var(--accent);font-weight:400;}
.panel{background:var(--card);border:1px solid var(--line);border-radius:20px;box-shadow:0 12px 40px rgba(98,66,43,0.06);padding:32px;}
.eyebrow{display:inline-flex;align-items:center;gap:8px;padding:8px 14px;border-radius:999px;background:rgba(192,57,27,0.06);border:1px solid rgba(192,57,27,0.08);color:var(--accent);font-size:12px;font-weight:800;letter-spacing:0.04em;text-transform:uppercase;margin-bottom:16px;}
.eyebrow::before{content:"";width:7px;height:7px;border-radius:50%;background:var(--accent);}
h1{font-family:'DM Serif Display',serif;font-size:clamp(32px,5vw,48px);line-height:1.05;letter-spacing:-0.04em;margin-bottom:12px;}
.lead{color:var(--soft);font-size:15px;line-height:1.7;}
.steps{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:20px;}
.step{padding:16px;border-radius:14px;background:rgba(255,255,255,0.6);border:1px solid rgba(84,52,29,0.04);text-align:center;}
.step-num{width:32px;height:32px;border-radius:50%;background:rgba(192,57,27,0.08);color:var(--accent);font-weight:800;font-size:14px;display:flex;align-items:center;justify-content:center;margin:0 auto 8px;}
.step strong{display:block;font-size:14px;margin-bottom:3px;}
.step span{color:var(--muted);font-size:12px;line-height:1.5;}
.status-card{padding:24px;border-radius:18px;background:rgba(255,255,255,0.6);border:1px solid var(--line);}
.status-card strong{display:block;font-size:20px;font-weight:800;margin-bottom:8px;}
.status-card p{color:var(--soft);font-size:14px;line-height:1.7;}
.actions{display:flex;flex-wrap:wrap;gap:10px;margin-top:16px;}
.btn{display:inline-flex;align-items:center;justify-content:center;padding:14px 22px;border-radius:12px;font-weight:800;font-size:14px;transition:transform .15s,box-shadow .15s;}
.btn:hover{transform:translateY(-1px);}
.btn-primary{background:linear-gradient(135deg,var(--accent),var(--accent-2));color:#fff;box-shadow:0 8px 24px rgba(192,57,27,0.22);}
.btn-ghost{background:rgba(255,255,255,0.7);border:1.5px solid var(--line);color:var(--ink);}
@media(max-width:600px){.steps{grid-template-columns:1fr;}}
</style>
</head>
<body>
<div class="shell">
  <a class="brand" href="/">Sinyal</a>
  <div class="panel">
    <div class="eyebrow">Lanjutkan setup</div>
    <h1>Tinggal satu langkah lagi.</h1>
    <p class="lead">Halaman ini bantu kamu lanjut ke langkah berikutnya tanpa bingung.</p>
    <div class="steps">
      <div class="step"><div class="step-num">1</div><strong>Bikin akun</strong><span>Supaya hasil riset tersimpan rapi.</span></div>
      <div class="step"><div class="step-num">2</div><strong>Aktifkan akses</strong><span>Pilih paket yang paling cocok.</span></div>
      <div class="step"><div class="step-num">3</div><strong>Mulai riset</strong><span>Langsung masuk ke app.</span></div>
    </div>
  </div>
  <div class="panel status-card">
    <strong id="nextStepTitle">Menyiapkan langkah berikutnya...</strong>
    <p id="nextStepBody">Tunggu sebentar.</p>
    <div class="actions">
      <a class="btn btn-primary" id="nextStepButton" href="/app">Lanjut</a>
      <a class="btn btn-ghost" href="/">Kembali ke beranda</a>
    </div>
  </div>
</div>
<script>
async function loadNextStep() {
  const title = document.getElementById('nextStepTitle');
  const body = document.getElementById('nextStepBody');
  const button = document.getElementById('nextStepButton');
  try {
    const resp = await fetch('/api/account/next-step');
    const data = await resp.json();
    title.textContent = data.title || 'Lanjutkan';
    body.textContent = data.message || 'Lanjut ke langkah berikutnya.';
    button.textContent = data.cta_label || 'Lanjut';
    button.href = data.target || '/app';
  } catch (e) {
    title.textContent = 'Lanjut ke workspace';
    body.textContent = 'Kamu tetap bisa lanjut manual ke app atau halaman paket.';
    button.textContent = 'Buka app';
    button.href = '/app';
  }
}
loadNextStep();
</script>
</body>
</html>"""


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def index():
    return LANDING_HTML


@app.get("/start", response_class=HTMLResponse)
async def start_page():
    return render_start_page()


@app.get("/signup", response_class=HTMLResponse)
async def signup_page():
    return render_public_account_page(
        title="Daftar Sinyal",
        heading="Daftar ke Sinyal",
        subheading="Buat akun baru. Gratis, tanpa kartu kredit.",
        primary_label="Daftar",
        secondary_label="Sudah punya akun? Masuk",
        secondary_href="/signin",
        show_google=True,
        mode="signup",
        form_fields="""
        <div class="field"><label>Nama lengkap</label><input id="signupFullName" type="text" placeholder="Nama kamu" required></div>
        <div class="field"><label>Email</label><input id="signupEmail" type="email" placeholder="nama@email.com" required></div>
        <div class="field"><label>Password</label><input id="signupPassword" type="password" placeholder="Minimal 8 karakter" minlength="8" required></div>
        <input type="hidden" id="signupRefCode" value="">
        <div id="refBanner" style="display:none;padding:10px 14px;border-radius:10px;background:rgba(40,95,88,0.08);border:1px solid rgba(40,95,88,0.15);font-size:13px;font-weight:700;color:#285f58;margin-top:4px;"></div>
        <div id="signupStatus" style="display:none;" class="note"></div>
        """,
        extra_script="""
        <script>
        document.getElementById('authForm')?.addEventListener('submit', async (e) => {
          e.preventDefault();
          const status = document.getElementById('signupStatus');
          status.style.display = 'block';
          const fullName = document.getElementById('signupFullName').value.trim();
          const email = document.getElementById('signupEmail').value.trim();
          const password = document.getElementById('signupPassword').value;
          if (!fullName) { status.textContent = 'Nama wajib diisi.'; return; }
          if (!email || !email.includes('@')) { status.textContent = 'Email tidak valid.'; return; }
          if (password.length < 8) { status.textContent = 'Password minimal 8 karakter.'; return; }
          status.textContent = 'Membuat akun...';
          const refCode = document.getElementById('signupRefCode').value.trim();
          const payload = { full_name: fullName, email, password };
          if (refCode) payload.referral_code = refCode;
          try {
            const res = await fetch('/api/auth/signup', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(payload),
            });
            const data = await res.json();
            if (!res.ok) {
              status.textContent = data.msg || data.error_description || data.error || 'Gagal daftar.';
              return;
            }
            status.style.background = 'rgba(40,95,88,0.12)';
            status.textContent = 'Akun berhasil dibuat! Mengalihkan...';
            window.location.href = '/app';
          } catch(e) { status.textContent = 'Koneksi gagal. Coba lagi.'; }
        });
        (function() {
          var params = new URLSearchParams(window.location.search);
          var ref = params.get('ref');
          if (ref) {
            document.getElementById('signupRefCode').value = ref;
            document.cookie = 'sinyal_ref=' + encodeURIComponent(ref) + '; path=/; max-age=604800; samesite=lax';
            var banner = document.getElementById('refBanner');
            banner.textContent = '\u2728 Kamu diundang lewat referral link. Selamat bergabung!';
            banner.style.display = 'block';
          }
        })();
        </script>
        """,
    )


@app.get("/signin", response_class=HTMLResponse)
async def signin_page():
    return render_public_account_page(
        title="Masuk Sinyal",
        heading="Masuk ke Sinyal",
        subheading="Selamat datang kembali. Masuk untuk lanjut riset.",
        primary_label="Masuk",
        secondary_label="Belum punya akun? Daftar",
        secondary_href="/signup",
        show_google=True,
        mode="signin",
        form_fields="""
        <div class="field"><label>Email</label><input id="signinEmail" type="email" placeholder="nama@email.com" required></div>
        <div class="field"><label>Password</label><input id="signinPassword" type="password" placeholder="Masukkan password" required></div>
        <div id="signinStatus" style="display:none;" class="note"></div>
        """,
        extra_script="""
        <script>
        document.getElementById('authForm')?.addEventListener('submit', async (e) => {
          e.preventDefault();
          const status = document.getElementById('signinStatus');
          status.style.display = 'block';
          const email = document.getElementById('signinEmail').value.trim();
          const password = document.getElementById('signinPassword').value;
          if (!email || !email.includes('@')) { status.textContent = 'Email tidak valid.'; return; }
          if (!password) { status.textContent = 'Password wajib diisi.'; return; }
          status.textContent = 'Sedang masuk...';
          try {
            const res = await fetch('/api/auth/signin', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ email, password }),
            });
            const data = await res.json();
            if (!res.ok) {
              status.textContent = data.msg || data.error_description || data.error || 'Gagal masuk.';
              return;
            }
            status.style.background = 'rgba(40,95,88,0.12)';
            status.textContent = 'Login berhasil! Mengalihkan...';
            window.location.href = '/app';
          } catch(e) { status.textContent = 'Koneksi gagal. Coba lagi.'; }
        });
        </script>
        """,
    )


@app.get("/payment", response_class=HTMLResponse)
async def payment_page():
    return render_payment_page()


@app.get("/checkout/{plan_code}")
async def checkout_plan(plan_code: str):
    plan = PLAN_CATALOG.get(plan_code)
    if not plan:
        return JSONResponse({"error": "Unknown plan"}, 404)

    checkout_url = os.getenv(plan["env_key"], "").strip()
    if not checkout_url:
        return HTMLResponse(
            f"""
            <html lang="id"><body style="font-family: sans-serif; padding: 32px">
            <h1>Checkout belum aktif</h1>
            <p>Link Mayar untuk <strong>{plan['name']}</strong> belum diisi di environment.</p>
            <p>Isi env <code>{plan['env_key']}</code> lalu buka lagi route ini.</p>
            <p><a href="/payment">Kembali ke halaman payment</a></p>
            </body></html>
            """,
            status_code=503,
        )

    return RedirectResponse(checkout_url, status_code=302)


@app.get("/api/billing/plans")
async def billing_plans():
    return {"plans": get_plan_catalog()}


@app.post("/api/auth/signup")
async def auth_signup(request: Request, payload: dict = Body(...)):
    rate_limited = enforce_rate_limit(request, "auth_signup")
    if rate_limited:
        return rate_limited
    email = normalize_text(payload.get("email"))
    password = payload.get("password", "")
    full_name = normalize_text(payload.get("full_name"))
    company_name = normalize_text(payload.get("company_name"))
    onboarding_use_case = normalize_text(payload.get("onboarding_use_case"))

    status_code, data = await supabase_auth_request(
        "/signup",
        {
            "email": email,
            "password": password,
            "data": {
                "full_name": full_name,
                "company_name": company_name,
                "onboarding_use_case": onboarding_use_case,
            },
        },
    )
    # Auto-confirm email via Admin API so users don't need to click a confirmation email
    user_id = (data.get("user") or {}).get("id")
    if status_code in (200, 201) and user_id and supabase_rest_configured():
        svc_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        supa_url = os.getenv("SUPABASE_URL", "").strip()
        if svc_key and supa_url:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.put(
                        f"{supa_url}/auth/v1/admin/users/{user_id}",
                        headers={
                            "apikey": svc_key,
                            "Authorization": f"Bearer {svc_key}",
                            "Content-Type": "application/json",
                        },
                        json={"email_confirm": True},
                    )
            except Exception:
                pass  # Non-fatal: user can confirm via email if admin call fails
    access_token = None
    refresh_token = None

    session = data.get("session") if isinstance(data, dict) else None
    if isinstance(session, dict):
      access_token = session.get("access_token")
      refresh_token = session.get("refresh_token")

    if not access_token and status_code in (200, 201) and email and password:
      signin_status, signin_data = await supabase_auth_request(
        "/token?grant_type=password",
        {
          "email": email,
          "password": password,
        },
      )
      if signin_status == 200 and isinstance(signin_data, dict):
        access_token = signin_data.get("access_token")
        refresh_token = signin_data.get("refresh_token")

    response = JSONResponse(data, status_code=status_code)
    set_auth_cookies(response, access_token, refresh_token)

    # ── Track affiliate referral if ref code was provided ──
    ref_code = normalize_text(payload.get("referral_code"))
    if ref_code and user_id and status_code in (200, 201):
        try:
            await _track_affiliate_referral(user_id, email, ref_code)
        except Exception as e:
            print(f"[WARN] affiliate referral tracking failed: {e}")

    return response


@app.post("/api/auth/signin")
async def auth_signin(request: Request, payload: dict = Body(...)):
    rate_limited = enforce_rate_limit(request, "auth_signin")
    if rate_limited:
        return rate_limited
    email = normalize_text(payload.get("email"))
    password = payload.get("password", "")

    # ── Owner account bypass ──
    if OWNER_EMAIL and email == OWNER_EMAIL.lower() and password == OWNER_PASSWORD:
        token = _owner_token()
        response = JSONResponse({"access_token": token, "token_type": "bearer"})
        set_auth_cookies(response, token, token)
        return response

    status_code, data = await supabase_auth_request(
        "/token?grant_type=password",
        {
            "email": email,
            "password": password,
        },
    )
    response = JSONResponse(data, status_code=status_code)
    set_auth_cookies(response, data.get("access_token"), data.get("refresh_token"))
    return response


@app.post("/api/auth/signout")
async def auth_signout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(AUTH_COOKIE_NAME)
    response.delete_cookie(REFRESH_COOKIE_NAME)
    return response


# ── Google OAuth ─────────────────────────────────────────────────
@app.get("/api/auth/google")
async def auth_google_initiate(request: Request):
    """Redirect user to Supabase → Google consent screen."""
    if not supabase_auth_configured():
        return HTMLResponse("<h3>Google Sign-In belum dikonfigurasi.</h3>", status_code=503)

    # Build our callback URL from the incoming request
    proto = "https" if request.headers.get("x-forwarded-proto") == "https" or request.url.scheme == "https" else "http"
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or str(request.base_url.netloc)
    callback_url = f"{proto}://{host}/auth/callback"

    # Supabase OAuth authorize endpoint
    authorize_url = (
        f"{SUPABASE_URL}/auth/v1/authorize"
        f"?provider=google"
        f"&redirect_to={callback_url}"
    )
    return RedirectResponse(authorize_url, status_code=302)


@app.get("/auth/callback")
async def auth_callback(request: Request):
    """
    Handle Supabase OAuth callback.
    Supabase can return tokens as:
      1) Query params  (PKCE flow): ?access_token=...&refresh_token=...
      2) Hash fragment (implicit):  #access_token=...&refresh_token=...
    For (2) the browser must forward fragments via JS, so we serve
    a tiny HTML page that reads location.hash and sets cookies via
    a follow-up POST/redirect.  For (1) we handle directly.
    """
    access_token = request.query_params.get("access_token")
    refresh_token = request.query_params.get("refresh_token")

    # ── Case 1: tokens already in query string ─────────────
    if access_token:
        response = RedirectResponse("/app", status_code=302)
        set_auth_cookies(response, access_token, refresh_token)

        # Try to track affiliate referral from cookie
        ref_code = request.cookies.get("sinyal_ref")
        if ref_code:
            try:
                jwt_data = decode_jwt_payload(access_token)
                uid = jwt_data.get("sub")
                email = jwt_data.get("email", "")
                if uid:
                    await _track_affiliate_referral(uid, email, ref_code)
            except Exception as e:
                print(f"[WARN] google oauth referral tracking failed: {e}")
            response.delete_cookie("sinyal_ref")

        return response

    # ── Case 2: Supabase returned a code (PKCE) ──────────
    code = request.query_params.get("code")
    if code:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{SUPABASE_URL}/auth/v1/token?grant_type=authorization_code",
                headers=supabase_headers(),
                json={"code": code},
            )
        if resp.status_code == 200:
            data = resp.json()
            at = data.get("access_token")
            rt = data.get("refresh_token")
            response = RedirectResponse("/app", status_code=302)
            set_auth_cookies(response, at, rt)

            ref_code = request.cookies.get("sinyal_ref")
            if ref_code and at:
                try:
                    jwt_data = decode_jwt_payload(at)
                    uid = jwt_data.get("sub")
                    email = jwt_data.get("email", "")
                    if uid:
                        await _track_affiliate_referral(uid, email, ref_code)
                except Exception as e:
                    print(f"[WARN] google oauth referral tracking failed: {e}")
                response.delete_cookie("sinyal_ref")

            return response
        else:
            return HTMLResponse(
                "<h3>Login gagal.</h3><p>Silakan <a href='/signin'>coba lagi</a>.</p>",
                status_code=400,
            )

    # ── Case 3: tokens in hash fragment – serve JS bridge page ──
    return HTMLResponse("""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Memproses login...</title>
<style>
body{display:flex;align-items:center;justify-content:center;min-height:100vh;
font-family:'Plus Jakarta Sans',sans-serif;background:#faf3ec;color:#232323;margin:0;}
.loader{text-align:center;}
.spinner{width:40px;height:40px;border:4px solid #e0d5c8;border-top-color:#c0391b;
border-radius:50%;animation:spin .8s linear infinite;margin:0 auto 16px;}
@keyframes spin{to{transform:rotate(360deg);}}
</style></head>
<body><div class="loader"><div class="spinner"></div><p>Memproses login...</p></div>
<script>
(function(){
  var h = window.location.hash.substring(1);
  if(!h){ window.location.href='/signin'; return; }
  var p = new URLSearchParams(h);
  var at = p.get('access_token');
  var rt = p.get('refresh_token');
  if(at){
    window.location.href='/auth/callback?access_token='+encodeURIComponent(at)
      +'&refresh_token='+encodeURIComponent(rt||'');
  } else {
    window.location.href='/signin';
  }
})();
</script></body></html>""", status_code=200)


@app.get("/api/auth/session")
async def auth_session(request: Request):
  if DEV_AUTH_BYPASS:
    return {
      "configured": True,
      "authenticated": True,
      "user": {"email": "dev@local", "id": "dev-local-user"},
      "profile": {
        "tier": "pro",
        "daily_searches_left": 999_999,
      },
    }

  # ── Owner bypass ──
  token = request.cookies.get(AUTH_COOKIE_NAME)
  if token and _is_owner_token(token):
    return {
      "configured": True,
      "authenticated": True,
      "user": {"email": OWNER_EMAIL, "id": OWNER_USER_ID},
      "profile": {
        "tier": "pro",
        "daily_searches_left": 999_999,
        "monthly_profiles_used": 0,
        "monthly_comments_used": 0,
        "allowed_platforms": ["tiktok", "instagram", "youtube", "twitter", "facebook"],
      },
    }

  if not supabase_auth_configured():
    return {"configured": False, "authenticated": False}
  user = await get_authenticated_user(request)
  profile = None
  if user and supabase_rest_configured():
    profile = await get_and_reset_profile_usage(user["id"])
  return {
    "configured": True,
    "authenticated": bool(user),
    "user": {"email": user["email"], "id": user["id"]} if user else None,
    "profile": profile,
  }


@app.get("/api/system/config")
async def system_config():
    plans = get_plan_catalog()
    return {
    "dev_auth_bypass": DEV_AUTH_BYPASS,
        "supabase_configured": supabase_auth_configured(),
        "supabase_rest_configured": supabase_rest_configured(),
        "mayar_ready": all(bool(plan["checkout_url"]) for plan in plans),
        "plans": [
            {
                "code": plan["code"],
                "has_checkout_url": bool(plan["checkout_url"]),
            }
            for plan in plans
        ],
    }


@app.get("/api/account/usage")
async def account_usage(request: Request):
  if DEV_AUTH_BYPASS:
    return {
      "configured": True,
      "database_ready": True,
      "user": {"email": "dev@local", "id": "dev-local-user"},
      "profile": {
        "tier": "pro",
        "daily_searches_left": 999_999,
      },
      "plan": {**PLAN_CATALOG["pro"], "code": "pro"},
    }

  if not supabase_auth_configured():
    return {"configured": False}

  user = await get_authenticated_user(request)
  if not user:
    return JSONResponse({"error": "Silakan login dulu."}, status_code=401)

  if not supabase_rest_configured():
    return {"configured": True, "database_ready": False, "user": user}

  profile = await get_and_reset_profile_usage(user["id"])
  tier = profile.get("tier", "free") if profile else "free"
  plan = PLAN_CATALOG.get(tier) or PLAN_CATALOG["free"]

  return {
    "configured": True,
    "database_ready": True,
    "user": {"email": user["email"], "id": user["id"]},
    "profile": profile,
    "plan": {**plan, "code": tier},
  }


@app.get("/api/account/next-step")
async def account_next_step(request: Request):
  if DEV_AUTH_BYPASS:
    return {
      "configured": True,
      "authenticated": True,
      "target": "/app",
      "title": "Mode dev aktif",
      "cta_label": "Masuk ke app",
    }

  if not supabase_auth_configured():
    return {
      "configured": False,
      "target": "/app", "title": "Workspace siap dibuka", "cta_label": "Buka app",
    }

  user = await get_authenticated_user(request)
  if not user:
    return {
      "configured": True, "authenticated": False,
      "target": "/signin", "title": "Masuk dulu", "cta_label": "Masuk",
    }

  profile = await get_and_reset_profile_usage(user["id"]) if supabase_rest_configured() else None
  tier = profile.get("tier", "free") if profile else "free"

  if tier != "free":
    return {
      "configured": True, "authenticated": True, "target": "/app",
      "title": "Akun Premium aktif", "cta_label": "Masuk ke app",
    }

  return {
    "configured": True, "authenticated": True, "target": "/payment",
        "title": "Pilih paket", "cta_label": "Lihat paket",
    }


def _saved_field(payload: dict, *keys: str, default=""):
  for key in keys:
    value = payload.get(key)
    if value not in (None, ""):
      return value
  return default


@app.get("/api/saved/playlists")
async def list_saved_playlists(request: Request):
  if not supabase_auth_configured():
    return JSONResponse({"error": "Silakan login dulu."}, status_code=401)

  user = await get_authenticated_user(request)
  if not user:
    return JSONResponse({"error": "Silakan login dulu."}, status_code=401)

  if not supabase_rest_configured():
    return JSONResponse({"error": "Database belum dikonfigurasi."}, status_code=503)

  status_code, data, _ = await supabase_rest_request(
    "GET",
    "/rest/v1/saved_playlists",
    params={
      "select": "id,name,created_at",
      "user_id": f"eq.{user['id']}",
      "order": "created_at.asc",
      "limit": "100",
    },
  )
  if status_code != 200:
    return JSONResponse({"error": "Gagal mengambil playlist."}, status_code=500)

  return {"playlists": data if isinstance(data, list) else []}


@app.post("/api/saved/playlists")
async def create_saved_playlist(request: Request, payload: dict = Body(...)):
  if not supabase_auth_configured():
    return JSONResponse({"error": "Silakan login dulu."}, status_code=401)

  user = await get_authenticated_user(request)
  if not user:
    return JSONResponse({"error": "Silakan login dulu."}, status_code=401)

  if not supabase_rest_configured():
    return JSONResponse({"error": "Database belum dikonfigurasi."}, status_code=503)

  name = normalize_text(payload.get("name") or "")
  if not name:
    return JSONResponse({"error": "Nama playlist wajib diisi."}, status_code=400)
  if len(name) > 80:
    return JSONResponse({"error": "Nama playlist maksimal 80 karakter."}, status_code=400)

  playlist_payload = {
    "user_id": user["id"],
    "name": name,
    "updated_at": datetime.now(timezone.utc).isoformat(),
  }
  status_code, data, _ = await supabase_rest_request(
    "POST",
    "/rest/v1/saved_playlists",
    payload=playlist_payload,
    prefer="resolution=merge-duplicates,return=representation",
  )
  if status_code not in (200, 201):
    return JSONResponse({"error": "Gagal membuat playlist."}, status_code=500)

  playlist = data[0] if isinstance(data, list) and data else None
  return {"ok": True, "playlist": playlist}


@app.get("/api/saved/items")
async def list_saved_items(request: Request, playlist_id: str | None = Query(None), limit: int = Query(100, ge=1, le=200)):
  if not supabase_auth_configured():
    return JSONResponse({"error": "Silakan login dulu."}, status_code=401)

  user = await get_authenticated_user(request)
  if not user:
    return JSONResponse({"error": "Silakan login dulu."}, status_code=401)

  if not supabase_rest_configured():
    return JSONResponse({"error": "Database belum dikonfigurasi."}, status_code=503)

  params = {
    "select": "id,playlist_id,platform,author,title,caption,transcript,video_url,thumbnail,views,likes,comments,shares,metadata,created_at",
    "user_id": f"eq.{user['id']}",
    "order": "created_at.desc",
    "limit": str(limit),
  }
  if playlist_id:
    params["playlist_id"] = f"eq.{playlist_id}"

  status_code, data, _ = await supabase_rest_request(
    "GET",
    "/rest/v1/saved_items",
    params=params,
  )
  if status_code != 200:
    return JSONResponse({"error": "Gagal mengambil saved items."}, status_code=500)

  return {"items": data if isinstance(data, list) else []}


@app.post("/api/saved/items")
async def create_saved_item(request: Request, payload: dict = Body(...)):
  if not supabase_auth_configured():
    return JSONResponse({"error": "Silakan login dulu."}, status_code=401)

  user = await get_authenticated_user(request)
  if not user:
    return JSONResponse({"error": "Silakan login dulu."}, status_code=401)

  if not supabase_rest_configured():
    return JSONResponse({"error": "Database belum dikonfigurasi."}, status_code=503)

  item = payload.get("item") if isinstance(payload.get("item"), dict) else payload
  playlist_id = normalize_text(payload.get("playlist_id") or "") or None

  video_url = normalize_text(_saved_field(item, "video_url", "url", default=""))
  title = normalize_text(_saved_field(item, "hook", "title", "caption", default="Tanpa judul"))

  def _as_int(value):
    try:
      if value in (None, ""):
        return 0
      return int(float(value))
    except (TypeError, ValueError):
      return 0

  if not video_url:
    return JSONResponse({"error": "video_url wajib diisi."}, status_code=400)

  insert_payload = {
    "user_id": user["id"],
    "playlist_id": playlist_id,
    "platform": normalize_text(_saved_field(item, "platform", default="")),
    "author": normalize_text(_saved_field(item, "author", default="")),
    "title": title,
    "caption": normalize_text(_saved_field(item, "caption", "content", "description", default="")),
    "transcript": normalize_text(_saved_field(item, "transcript", default="")),
    "video_url": video_url,
    "thumbnail": normalize_text(_saved_field(item, "thumbnail", default="")),
    "views": _as_int(item.get("views")),
    "likes": _as_int(item.get("likes")),
    "comments": _as_int(item.get("comments")),
    "shares": _as_int(item.get("shares")),
    "metadata": {
      "hashtags": item.get("hashtags") or [],
      "music": item.get("music") or "",
      "saved_from": payload.get("source") or "search",
    },
    "updated_at": datetime.now(timezone.utc).isoformat(),
  }

  status_code, existing_rows, _ = await supabase_rest_request(
    "GET",
    "/rest/v1/saved_items",
    params={
      "select": "id",
      "user_id": f"eq.{user['id']}",
      "video_url": f"eq.{video_url}",
      "playlist_id": f"eq.{playlist_id}" if playlist_id else "is.null",
      "limit": "1",
    },
  )
  if status_code == 200 and isinstance(existing_rows, list) and existing_rows:
    return {"ok": True, "already_saved": True, "item_id": existing_rows[0].get("id")}

  status_code, data, _ = await supabase_rest_request(
    "POST",
    "/rest/v1/saved_items",
    payload=insert_payload,
    prefer="return=representation",
  )
  if status_code not in (200, 201):
    return JSONResponse({"error": "Gagal menyimpan item."}, status_code=500)

  saved_item = data[0] if isinstance(data, list) and data else None
  return {"ok": True, "already_saved": False, "item": saved_item}


@app.delete("/api/saved/items/{item_id}")
async def delete_saved_item(item_id: str, request: Request):
  if not supabase_auth_configured():
    return JSONResponse({"error": "Silakan login dulu."}, status_code=401)

  user = await get_authenticated_user(request)
  if not user:
    return JSONResponse({"error": "Silakan login dulu."}, status_code=401)

  if not supabase_rest_configured():
    return JSONResponse({"error": "Database belum dikonfigurasi."}, status_code=503)

  status_code, _, _ = await supabase_rest_request(
    "DELETE",
    f"/rest/v1/saved_items?id=eq.{item_id}&user_id=eq.{user['id']}",
    prefer="return=minimal",
  )
  if status_code not in (200, 204):
    return JSONResponse({"error": "Gagal menghapus item."}, status_code=500)
  return {"ok": True}


# ════════════════════════════════════════════════════════════════════════
# AFFILIATE SYSTEM
# ════════════════════════════════════════════════════════════════════════

import secrets as _secrets

AFFILIATE_DEFAULT_COMMISSION_PCT = int(os.getenv("AFFILIATE_COMMISSION_PCT", "20"))
AFFILIATE_MIN_PAYOUT_IDR = int(os.getenv("AFFILIATE_MIN_PAYOUT_IDR", "50000"))


def _generate_referral_code(email: str) -> str:
    """Generate a short, unique referral code based on email + random suffix."""
    prefix = re.sub(r"[^a-z0-9]", "", email.split("@")[0].lower())[:8]
    suffix = _secrets.token_hex(3)  # 6 hex chars
    return f"{prefix}_{suffix}"


@app.get("/api/affiliate/me")
async def affiliate_me(request: Request):
    """Get current user's affiliate info. Returns null affiliate if not yet activated."""
    if DEV_AUTH_BYPASS:
        return {
            "affiliate": {
                "referral_code": "devlocal_abc123",
                "commission_pct": 20,
                "lifetime_earnings": 198000,
                "pending_balance": 99000,
                "paid_out": 99000,
                "referral_count": 5,
                "paid_referral_count": 2,
                "is_active": True,
                "payout_method": None,
                "payout_detail": {},
            },
            "referral_url": "http://localhost:8000/signup?ref=devlocal_abc123",
        }

    if not supabase_auth_configured():
        return JSONResponse({"error": "Silakan login dulu."}, status_code=401)
    user = await get_authenticated_user(request)
    if not user:
        return JSONResponse({"error": "Silakan login dulu."}, status_code=401)
    if not supabase_rest_configured():
        return JSONResponse({"error": "Database belum dikonfigurasi."}, status_code=503)

    status_code, data, _ = await supabase_rest_request(
        "GET",
        "/rest/v1/affiliates",
        params={"user_id": f"eq.{user['id']}", "select": "*", "limit": "1"},
    )
    aff = data[0] if status_code == 200 and isinstance(data, list) and data else None
    if aff:
        base_url = str(request.base_url).rstrip("/")
        return {
            "affiliate": aff,
            "referral_url": f"{base_url}/signup?ref={aff['referral_code']}",
        }
    return {"affiliate": None, "referral_url": None}


@app.post("/api/affiliate/activate")
async def affiliate_activate(request: Request):
    """Activate affiliate account for current user."""
    if DEV_AUTH_BYPASS:
        return {"ok": True, "referral_code": "devlocal_abc123"}

    if not supabase_auth_configured():
        return JSONResponse({"error": "Silakan login dulu."}, status_code=401)
    user = await get_authenticated_user(request)
    if not user:
        return JSONResponse({"error": "Silakan login dulu."}, status_code=401)
    if not supabase_rest_configured():
        return JSONResponse({"error": "Database belum dikonfigurasi."}, status_code=503)

    # Check if already activated
    status_code, data, _ = await supabase_rest_request(
        "GET",
        "/rest/v1/affiliates",
        params={"user_id": f"eq.{user['id']}", "select": "referral_code", "limit": "1"},
    )
    if status_code == 200 and isinstance(data, list) and data:
        return {"ok": True, "referral_code": data[0]["referral_code"], "already_active": True}

    code = _generate_referral_code(user.get("email", "user"))
    status_code, data, _ = await supabase_rest_request(
        "POST",
        "/rest/v1/affiliates",
        payload={
            "user_id": user["id"],
            "referral_code": code,
            "commission_pct": AFFILIATE_DEFAULT_COMMISSION_PCT,
        },
        prefer="return=representation",
    )
    if status_code not in (200, 201):
        return JSONResponse({"error": "Gagal mengaktifkan affiliate.", "detail": data}, status_code=500)
    return {"ok": True, "referral_code": code}


@app.get("/api/affiliate/referrals")
async def affiliate_referrals(request: Request):
    """List all referrals for the current affiliate."""
    if DEV_AUTH_BYPASS:
        return {
            "referrals": [
                {"id": "r1", "referred_email": "andi@mail.com", "status": "converted", "converted_plan": "pro", "converted_amount": 99000, "commission_amount": 19800, "signed_up_at": "2026-03-25T10:00:00Z", "converted_at": "2026-03-26T14:00:00Z"},
                {"id": "r2", "referred_email": "budi@mail.com", "status": "converted", "converted_plan": "starter", "converted_amount": 49000, "commission_amount": 9800, "signed_up_at": "2026-03-20T08:00:00Z", "converted_at": "2026-03-21T12:00:00Z"},
                {"id": "r3", "referred_email": "cici@mail.com", "status": "signed_up", "converted_plan": None, "converted_amount": 0, "commission_amount": 0, "signed_up_at": "2026-03-27T09:00:00Z", "converted_at": None},
                {"id": "r4", "referred_email": "dina@mail.com", "status": "signed_up", "converted_plan": None, "converted_amount": 0, "commission_amount": 0, "signed_up_at": "2026-03-28T07:00:00Z", "converted_at": None},
                {"id": "r5", "referred_email": "eka@mail.com", "status": "signed_up", "converted_plan": None, "converted_amount": 0, "commission_amount": 0, "signed_up_at": "2026-03-28T11:00:00Z", "converted_at": None},
            ]
        }

    if not supabase_auth_configured():
        return JSONResponse({"error": "Silakan login dulu."}, status_code=401)
    user = await get_authenticated_user(request)
    if not user:
        return JSONResponse({"error": "Silakan login dulu."}, status_code=401)
    if not supabase_rest_configured():
        return JSONResponse({"error": "Database belum dikonfigurasi."}, status_code=503)

    # Get affiliate id
    st, aff_data, _ = await supabase_rest_request(
        "GET", "/rest/v1/affiliates",
        params={"user_id": f"eq.{user['id']}", "select": "id", "limit": "1"},
    )
    if st != 200 or not isinstance(aff_data, list) or not aff_data:
        return {"referrals": []}

    aff_id = aff_data[0]["id"]
    st2, refs, _ = await supabase_rest_request(
        "GET", "/rest/v1/affiliate_referrals",
        params={
            "affiliate_id": f"eq.{aff_id}",
            "select": "id,referred_email,status,converted_plan,converted_amount,commission_amount,signed_up_at,converted_at",
            "order": "signed_up_at.desc",
            "limit": "100",
        },
    )
    return {"referrals": refs if st2 == 200 and isinstance(refs, list) else []}


@app.get("/api/affiliate/payouts")
async def affiliate_payouts(request: Request):
    """List payout history for the current affiliate."""
    if DEV_AUTH_BYPASS:
        return {
            "payouts": [
                {"id": "p1", "amount": 99000, "status": "completed", "payout_method": "bank_transfer", "requested_at": "2026-03-20T10:00:00Z", "processed_at": "2026-03-22T09:00:00Z"},
            ],
            "min_payout": AFFILIATE_MIN_PAYOUT_IDR,
        }

    if not supabase_auth_configured():
        return JSONResponse({"error": "Silakan login dulu."}, status_code=401)
    user = await get_authenticated_user(request)
    if not user:
        return JSONResponse({"error": "Silakan login dulu."}, status_code=401)
    if not supabase_rest_configured():
        return JSONResponse({"error": "Database belum dikonfigurasi."}, status_code=503)

    st, aff_data, _ = await supabase_rest_request(
        "GET", "/rest/v1/affiliates",
        params={"user_id": f"eq.{user['id']}", "select": "id", "limit": "1"},
    )
    if st != 200 or not isinstance(aff_data, list) or not aff_data:
        return {"payouts": [], "min_payout": AFFILIATE_MIN_PAYOUT_IDR}

    aff_id = aff_data[0]["id"]
    st2, payouts, _ = await supabase_rest_request(
        "GET", "/rest/v1/affiliate_payouts",
        params={
            "affiliate_id": f"eq.{aff_id}",
            "select": "id,amount,status,payout_method,requested_at,processed_at,admin_note",
            "order": "requested_at.desc",
            "limit": "50",
        },
    )
    return {"payouts": payouts if st2 == 200 and isinstance(payouts, list) else [], "min_payout": AFFILIATE_MIN_PAYOUT_IDR}


@app.post("/api/affiliate/payout-settings")
async def affiliate_payout_settings(request: Request, payload: dict = Body(...)):
    """Update payout method & detail."""
    if DEV_AUTH_BYPASS:
        return {"ok": True}

    if not supabase_auth_configured():
        return JSONResponse({"error": "Silakan login dulu."}, status_code=401)
    user = await get_authenticated_user(request)
    if not user:
        return JSONResponse({"error": "Silakan login dulu."}, status_code=401)
    if not supabase_rest_configured():
        return JSONResponse({"error": "Database belum dikonfigurasi."}, status_code=503)

    method = normalize_text(payload.get("payout_method"))
    detail = payload.get("payout_detail", {})
    if not method:
        return JSONResponse({"error": "Pilih metode payout."}, status_code=400)

    st, _, _ = await supabase_rest_request(
        "PATCH",
        f"/rest/v1/affiliates?user_id=eq.{user['id']}",
        payload={"payout_method": method, "payout_detail": detail, "updated_at": datetime.now(timezone.utc).isoformat()},
    )
    if st not in (200, 204):
        return JSONResponse({"error": "Gagal update pengaturan payout."}, status_code=500)
    return {"ok": True}


@app.post("/api/affiliate/request-payout")
async def affiliate_request_payout(request: Request):
    """Request a payout of pending balance."""
    if DEV_AUTH_BYPASS:
        return {"ok": True, "message": "Payout request submitted (dev mode)."}

    if not supabase_auth_configured():
        return JSONResponse({"error": "Silakan login dulu."}, status_code=401)
    user = await get_authenticated_user(request)
    if not user:
        return JSONResponse({"error": "Silakan login dulu."}, status_code=401)
    if not supabase_rest_configured():
        return JSONResponse({"error": "Database belum dikonfigurasi."}, status_code=503)

    st, aff_data, _ = await supabase_rest_request(
        "GET", "/rest/v1/affiliates",
        params={"user_id": f"eq.{user['id']}", "select": "*", "limit": "1"},
    )
    if st != 200 or not isinstance(aff_data, list) or not aff_data:
        return JSONResponse({"error": "Affiliate belum diaktifkan."}, status_code=400)

    aff = aff_data[0]
    pending = int(aff.get("pending_balance", 0))
    if pending < AFFILIATE_MIN_PAYOUT_IDR:
        return JSONResponse({"error": f"Saldo minimum untuk payout: Rp{AFFILIATE_MIN_PAYOUT_IDR:,}".replace(",", ".")}, status_code=400)

    if not aff.get("payout_method"):
        return JSONResponse({"error": "Isi dulu metode payout di pengaturan."}, status_code=400)

    # Create payout record
    st2, _, _ = await supabase_rest_request(
        "POST", "/rest/v1/affiliate_payouts",
        payload={
            "affiliate_id": aff["id"],
            "amount": pending,
            "status": "pending",
            "payout_method": aff.get("payout_method"),
            "payout_detail": aff.get("payout_detail", {}),
        },
        prefer="return=minimal",
    )
    if st2 not in (200, 201):
        return JSONResponse({"error": "Gagal membuat request payout."}, status_code=500)

    # Reset pending balance
    await supabase_rest_request(
        "PATCH",
        f"/rest/v1/affiliates?id=eq.{aff['id']}",
        payload={"pending_balance": 0, "paid_out": int(aff.get("paid_out", 0)) + pending, "updated_at": datetime.now(timezone.utc).isoformat()},
    )
    return {"ok": True, "amount": pending, "message": f"Request payout Rp{pending:,} berhasil diajukan.".replace(",", ".")}


async def _track_affiliate_referral(referred_user_id: str, referred_email: str, referral_code: str):
    """Internal: called during signup to record a referral."""
    if not supabase_rest_configured():
        return
    # Find affiliate by code
    st, aff_data, _ = await supabase_rest_request(
        "GET", "/rest/v1/affiliates",
        params={"referral_code": f"eq.{referral_code}", "is_active": "eq.true", "select": "id", "limit": "1"},
    )
    if st != 200 or not isinstance(aff_data, list) or not aff_data:
        return
    aff_id = aff_data[0]["id"]

    # Insert referral record
    await supabase_rest_request(
        "POST", "/rest/v1/affiliate_referrals",
        payload={
            "affiliate_id": aff_id,
            "referred_user_id": referred_user_id,
            "referred_email": referred_email,
            "status": "signed_up",
        },
        prefer="return=minimal",
    )
    # Increment referral_count
    await supabase_rest_request(
        "PATCH",
        f"/rest/v1/affiliates?id=eq.{aff_id}",
        payload={"referral_count": int((aff_data[0] if aff_data else {}).get("referral_count", 0)) + 1, "updated_at": datetime.now(timezone.utc).isoformat()},
    )


async def _credit_affiliate_commission(payer_email: str, plan_code: str, amount: int):
    """Internal: called when payment succeeds to credit the affiliate who referred this user."""
    if not supabase_rest_configured():
        return
    # Find the user_id of the payer
    st, profiles, _ = await supabase_rest_request(
        "GET", "/rest/v1/profiles",
        params={"email": f"eq.{payer_email}", "select": "user_id", "limit": "1"},
    )
    if st != 200 or not isinstance(profiles, list) or not profiles:
        return
    payer_user_id = profiles[0].get("user_id") or profiles[0].get("id")

    # Find the referral record for this user
    st2, refs, _ = await supabase_rest_request(
        "GET", "/rest/v1/affiliate_referrals",
        params={"referred_user_id": f"eq.{payer_user_id}", "status": "eq.signed_up", "select": "id,affiliate_id", "limit": "1"},
    )
    if st2 != 200 or not isinstance(refs, list) or not refs:
        return
    ref = refs[0]
    aff_id = ref["affiliate_id"]

    # Get affiliate commission %
    st3, aff_data, _ = await supabase_rest_request(
        "GET", "/rest/v1/affiliates",
        params={"id": f"eq.{aff_id}", "select": "*", "limit": "1"},
    )
    if st3 != 200 or not isinstance(aff_data, list) or not aff_data:
        return
    aff = aff_data[0]
    commission_pct = int(aff.get("commission_pct", AFFILIATE_DEFAULT_COMMISSION_PCT))
    commission = int(amount * commission_pct / 100)

    # Update referral record to converted
    await supabase_rest_request(
        "PATCH",
        f"/rest/v1/affiliate_referrals?id=eq.{ref['id']}",
        payload={
            "status": "converted",
            "converted_plan": plan_code,
            "converted_amount": amount,
            "commission_amount": commission,
            "converted_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    # Update affiliate totals
    new_lifetime = int(aff.get("lifetime_earnings", 0)) + commission
    new_pending = int(aff.get("pending_balance", 0)) + commission
    new_paid_count = int(aff.get("paid_referral_count", 0)) + 1
    await supabase_rest_request(
        "PATCH",
        f"/rest/v1/affiliates?id=eq.{aff_id}",
        payload={
            "lifetime_earnings": new_lifetime,
            "pending_balance": new_pending,
            "paid_referral_count": new_paid_count,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    print(f"[AFFILIATE] Credited {commission} IDR to affiliate {aff_id} for referral {ref['id']}")


@app.post("/api/payment/webhook/mayar")
async def mayar_webhook(request: Request, payload: dict = Body(...)):
    if not mayar_secret_matches(request):
        return JSONResponse({"error": "Webhook secret tidak valid."}, status_code=401)

    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    amount = int(data.get("amount") or payload.get("amount") or 0)
    payer_email = normalize_text(
        data.get("customerEmail") or data.get("email") or payload.get("customerEmail") or payload.get("email")
    ).lower()

    raw_status = normalize_text(data.get("status") or payload.get("status") or payload.get("event") or "").lower()
    
    if raw_status not in {"paid", "success", "settled", "completed", "true", "active"}:
        return {"received": True, "status": "ignored_not_paid"}

    # Find plan tier from amount
    tier = "free"
    for code, plan in PLAN_CATALOG.items():
        if amount == plan["price_idr"]:
            tier = code
            break
            
    if tier == "free":
        return {"received": True, "status": "ignored_unknown_amount"}

    # Find user ID by email via profiles
    status_code, profiles, _ = await supabase_rest_request("GET", "/rest/v1/profiles", params={"email": f"eq.{payer_email}", "select": "id", "limit": "1"})
    
    if status_code == 200 and isinstance(profiles, list) and profiles:
        user_id = profiles[0]["id"]
        plan = PLAN_CATALOG[tier]
        # Update user profile to new tier
        await supabase_rest_request(
            "PATCH",
            f"/rest/v1/profiles?id=eq.{user_id}",
            payload={"tier": tier, "daily_searches_left": plan["daily_search_limit"], "last_search_reset": datetime.now(timezone.utc).isoformat()}
        )
        # ── Credit affiliate commission if this user was referred ──
        try:
            await _credit_affiliate_commission(payer_email, tier, amount)
        except Exception as e:
            print(f"[WARN] affiliate commission credit failed: {e}")

        return {"received": True, "provider": "mayar", "status": "upgraded", "email": payer_email, "new_tier": tier}

    return {"received": True, "provider": "mayar", "status": "profile_not_found"}

@app.get("/app", response_class=HTMLResponse)
async def app_page(request: Request):
    # Allow unauthenticated access for FREE tier
    return APP_HTML


@app.get("/account", response_class=HTMLResponse)
async def account_page(request: Request):
    return ACCOUNT_HTML


@app.get("/affiliate", response_class=HTMLResponse)
async def affiliate_page():
    return AFFILIATE_HTML


@app.get("/api/affiliate/public-stats")
async def affiliate_public_stats():
    """Public endpoint — returns aggregate affiliate stats for social proof."""
    try:
        # Total active affiliates
        st1, d1, _ = await supabase_rest_request(
            "GET", "/rest/v1/affiliates",
            params={"is_active": "eq.true", "select": "id"},
        )
        total_affiliates = len(d1) if st1 == 200 and isinstance(d1, list) else 0

        # Total paid out
        st2, d2, _ = await supabase_rest_request(
            "GET", "/rest/v1/affiliates",
            params={"select": "lifetime_earnings,paid_out"},
        )
        total_earned = 0
        total_paid = 0
        if st2 == 200 and isinstance(d2, list):
            for a in d2:
                total_earned += int(a.get("lifetime_earnings", 0))
                total_paid += int(a.get("paid_out", 0))

        # Total referrals
        st3, d3, _ = await supabase_rest_request(
            "GET", "/rest/v1/affiliate_referrals",
            params={"select": "id"},
        )
        total_referrals = len(d3) if st3 == 200 and isinstance(d3, list) else 0

        return {
            "total_affiliates": total_affiliates,
            "total_referrals": total_referrals,
            "total_earned": total_earned,
            "total_paid": total_paid,
            "commission_pct": AFFILIATE_DEFAULT_COMMISSION_PCT,
            "min_payout": AFFILIATE_MIN_PAYOUT_IDR,
        }
    except Exception as e:
        print(f"[WARN] affiliate public stats failed: {e}")
        return {
            "total_affiliates": 0,
            "total_referrals": 0,
            "total_earned": 0,
            "total_paid": 0,
            "commission_pct": AFFILIATE_DEFAULT_COMMISSION_PCT,
            "min_payout": AFFILIATE_MIN_PAYOUT_IDR,
        }


@app.get("/api/search")
async def search(
    request: Request,
    q: str | None = Query(None),
    keyword: str | None = Query(None),
    platforms: str = Query("tiktok,youtube,instagram,twitter,facebook"),
    max: int | None = Query(None, ge=1, le=50),
    max_results: int | None = Query(None, ge=1, le=50),
    sort: str = Query("relevance"),
    date_range: str = Query("all"),
    min_views: int | None = Query(None),
    max_views: int | None = Query(None),
    min_likes: int | None = Query(None),
    max_likes: int | None = Query(None),
):
    rate_limited = enforce_rate_limit(request, "search")
    if rate_limited:
        return rate_limited

    user, plan, denial = await enforce_feature_access(request, "search")
    if denial:
        return denial

    query_value = (q or keyword or "").strip()
    if not query_value:
        return JSONResponse({"error": "Query wajib diisi via `q` atau `keyword`."}, 400)

    max_value = max_results or max or 5

    cache_key = (
        query_value,
        platforms,
        max_value,
        sort,
        date_range,
        min_views,
        max_views,
        min_likes,
        max_likes,
    )
    # Increment daily search counter for IP (happens for both cached and fresh)
    _increment_ip_daily_search(request)

    cached = SEARCH_CACHE.get(cache_key)
    if cached and time.time() - cached[0] < SEARCH_CACHE_TTL_SECONDS:
      if user and plan and supabase_rest_configured():
        profile = user.get("profile", {})
        left = int(profile.get("daily_searches_left", 0) or 0)
        if left > 0:
          await decrement_search_limit(user["id"], left)
        return {
            **cached[1],
            "cached": True,
            "elapsed": "<1s (cached)",
            "json_file": cached[1].get("json_file"),
            "csv_file": cached[1].get("csv_file"),
            "pdf_file": cached[1].get("pdf_file"),
        }

    platform_list = [p.strip() for p in platforms.split(",") if p.strip() in SCRAPERS]
    if not platform_list:
        return JSONResponse({"error": "No valid platforms"}, 400)

    # Enforce platform restrictions per plan
    if plan:
        allowed = plan.get("allowed_platforms") or list(SCRAPERS.keys())
        platform_list = [p for p in platform_list if p in allowed]
        if not platform_list:
            return JSONResponse(
                {
                    "error": f"Paket {plan.get('name', 'Free')} hanya mendukung: {', '.join(allowed)}. Upgrade untuk platform lain.",
                    "code": "platform_restricted",
                    "allowed_platforms": allowed,
                    "upgrade_url": "/payment",
                },
                400,
            )

    # Support multiple keywords separated by newlines
    keywords = [k.strip() for k in query_value.split("\n") if k.strip()]
    if not keywords:
        return JSONResponse({"error": "No keywords provided"}, 400)

    all_results = []
    start = time.time()

    async def scrape_platform(name, keyword):
        task_cache_key = (
            name,
            keyword,
            max_value,
            sort,
            min_likes,
            max_likes,
        )
        cached_task = PLATFORM_SEARCH_CACHE.get(task_cache_key)
        if cached_task and time.time() - cached_task[0] < SEARCH_CACHE_TTL_SECONDS:
            return cached_task[1]

        scraper = SCRAPERS[name]()
        try:
            if name == "tiktok":
                coro = scraper.search(
                    keyword,
                    max_value,
                    sort=sort,
                    min_likes=min_likes,
                    max_likes=max_likes,
                )
            else:
                coro = scraper.search(keyword, max_value)

            async with _get_browser_sem():
                results = await asyncio.wait_for(
                    coro,
                    timeout=SCRAPE_TIMEOUT_SECONDS,
                )
            PLATFORM_SEARCH_CACHE[task_cache_key] = (time.time(), results)
            return results
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                f"{name} scrape timed out after {SCRAPE_TIMEOUT_SECONDS}s"
            ) from exc

    task_specs = [
      (platform_name, keyword_item)
      for keyword_item in keywords
      for platform_name in platform_list
    ]
    tasks = [
      scrape_platform(platform_name, keyword_item)
      for platform_name, keyword_item in task_specs
    ]
    results_list = await asyncio.gather(*tasks, return_exceptions=True)

    platform_breakdown = {
      platform_name: {
        "results": 0,
        "errors": [],
      }
      for platform_name in platform_list
    }

    for index, result in enumerate(results_list):
      platform_name, keyword_item = task_specs[index]
      if isinstance(result, Exception):
        print(f"Error: {result}")
        platform_breakdown[platform_name]["errors"].append({
          "keyword": keyword_item,
          "message": str(result)[:160],
        })
      elif result:
        all_results.extend(result)
        platform_breakdown[platform_name]["results"] += len(result)

    # NOTE: No more fake fallback padding. Only real scraped results are returned.
    # If a platform returns fewer than max_value, the user simply sees fewer cards
    # — far better than showing junk "fallback" placeholders in a paid product.

    all_results = [enrich_result_text(result) for result in all_results]

    # Apply filters
    if date_range != "all":
        all_results = filter_results_by_date_range(all_results, date_range)
    if min_views is not None:
        all_results = [r for r in all_results if (r.views or 0) >= min_views]
    if max_views is not None:
        all_results = [r for r in all_results if (r.views or 0) <= max_views]
    if min_likes is not None:
        all_results = [r for r in all_results if (r.likes or 0) >= min_likes]
    if max_likes is not None:
        all_results = [r for r in all_results if (r.likes or 0) <= max_likes]

    # Apply sort
    if sort == "popular":
        all_results.sort(key=lambda r: r.views or 0, reverse=True)
    elif sort == "latest":
        all_results.sort(key=lambda r: r.upload_date or "", reverse=True)
    elif sort == "most_liked":
        all_results.sort(key=lambda r: r.likes or 0, reverse=True)

    elapsed = time.time() - start

    json_file = csv_file = pdf_file = None
    watermark = bool(plan and plan.get("watermark_exports"))
    if all_results:
        json_file, csv_file, pdf_file = save_results(all_results, keywords[0], watermark=watermark)

    payload = {
        "keywords": keywords,
        "platforms": platform_list,
      "platform_breakdown": platform_breakdown,
        "total": len(all_results),
        "elapsed": f"{elapsed:.1f}s",
        "cached": False,
        "plan_code": plan.get("code") if plan else None,
        "json_file": json_file,
        "csv_file": csv_file,
        "pdf_file": pdf_file,
        "results": [r.to_dict() for r in all_results],
    }
    SEARCH_CACHE[cache_key] = (time.time(), payload)
    
    if user and supabase_rest_configured():
        profile = user.get("profile", {})
        left = int(profile.get("daily_searches_left", 0))
        if left > 0:
            await decrement_search_limit(user["id"], left)
    return payload


# ── SSE Streaming Search ─────────────────────────────────────────────
@app.get("/api/search/stream")
async def search_stream(
    request: Request,
    q: str | None = Query(None),
    keyword: str | None = Query(None),
    platforms: str = Query("tiktok,youtube,instagram,twitter,facebook"),
    max: int | None = Query(None, ge=1, le=50),
    max_results: int | None = Query(None, ge=1, le=50),
    sort: str = Query("relevance"),
    date_range: str = Query("all"),
    min_views: int | None = Query(None),
    max_views: int | None = Query(None),
    min_likes: int | None = Query(None),
    max_likes: int | None = Query(None),
):
    """Stream search results per-platform via SSE so the UI can show progress."""
    rate_limited = enforce_rate_limit(request, "search")
    if rate_limited:
        return rate_limited

    user, plan, denial = await enforce_feature_access(request, "search")
    if denial:
        return denial

    query_value = (q or keyword or "").strip()
    if not query_value:
        return JSONResponse({"error": "Query wajib diisi."}, 400)

    max_value = max_results or max or 5
    platform_list = [p.strip() for p in platforms.split(",") if p.strip() in SCRAPERS]
    if not platform_list:
        return JSONResponse({"error": "Tidak ada platform valid."}, 400)

    if plan:
        allowed = plan.get("allowed_platforms") or list(SCRAPERS.keys())
        platform_list = [p for p in platform_list if p in allowed]

    keywords = [k.strip() for k in query_value.split("\n") if k.strip()]
    if not keywords:
        return JSONResponse({"error": "No keywords provided"}, 400)

    async def event_generator():
        import json as _json
        all_results = []
        start = time.time()
        total_tasks = len(platform_list) * len(keywords)
        completed = 0

        platform_breakdown = {p: {"results": 0, "errors": []} for p in platform_list}

        # Send init event
        yield f"data: {_json.dumps({'type': 'init', 'platforms': platform_list, 'total_tasks': total_tasks})}\n\n"

        async def scrape_one(name, kw):
            nonlocal completed
            task_cache_key = (name, kw, max_value, sort, min_likes, max_likes)
            cached = PLATFORM_SEARCH_CACHE.get(task_cache_key)
            if cached and time.time() - cached[0] < SEARCH_CACHE_TTL_SECONDS:
                return name, kw, cached[1], None
            scraper = SCRAPERS[name]()
            try:
                if name == "tiktok":
                    coro = scraper.search(kw, max_value, sort=sort, min_likes=min_likes, max_likes=max_likes)
                else:
                    coro = scraper.search(kw, max_value)
                async with _get_browser_sem():
                    results = await asyncio.wait_for(coro, timeout=SCRAPE_TIMEOUT_SECONDS)
                PLATFORM_SEARCH_CACHE[task_cache_key] = (time.time(), results)
                return name, kw, results, None
            except Exception as exc:
                return name, kw, None, str(exc)[:160]

        # Run with asyncio.as_completed so we can stream each as it finishes
        task_specs = [(p, kw) for kw in keywords for p in platform_list]
        coros = [scrape_one(name, kw) for name, kw in task_specs]
        tasks = [asyncio.ensure_future(c) for c in coros]

        for future in asyncio.as_completed(tasks):
            name, kw, results, error = await future
            completed += 1
            pct = int(completed / total_tasks * 100)

            if error:
                platform_breakdown[name]["errors"].append({"keyword": kw, "message": error})
                yield f"data: {_json.dumps({'type': 'platform_error', 'platform': name, 'keyword': kw, 'error': error, 'progress': pct, 'completed': completed, 'total': total_tasks})}\n\n"
            elif results:
                enriched = [enrich_result_text(r) for r in results]
                all_results.extend(enriched)
                platform_breakdown[name]["results"] += len(enriched)
                yield f"data: {_json.dumps({'type': 'platform_done', 'platform': name, 'keyword': kw, 'count': len(enriched), 'results': [r.to_dict() for r in enriched], 'progress': pct, 'completed': completed, 'total': total_tasks})}\n\n"
            else:
                yield f"data: {_json.dumps({'type': 'platform_done', 'platform': name, 'keyword': kw, 'count': 0, 'results': [], 'progress': pct, 'completed': completed, 'total': total_tasks})}\n\n"

        # Apply filters & sort on the combined results
        if date_range != "all":
            all_results = filter_results_by_date_range(all_results, date_range)
        if min_views is not None:
            all_results = [r for r in all_results if (r.views or 0) >= min_views]
        if max_views is not None:
            all_results = [r for r in all_results if (r.views or 0) <= max_views]
        if min_likes is not None:
            all_results = [r for r in all_results if (r.likes or 0) >= min_likes]
        if max_likes is not None:
            all_results = [r for r in all_results if (r.likes or 0) <= max_likes]
        if sort == "popular":
            all_results.sort(key=lambda r: r.views or 0, reverse=True)
        elif sort == "latest":
            all_results.sort(key=lambda r: r.upload_date or "", reverse=True)
        elif sort == "most_liked":
            all_results.sort(key=lambda r: r.likes or 0, reverse=True)

        elapsed = time.time() - start
        watermark = bool(plan and plan.get("watermark_exports"))
        json_file = csv_file = pdf_file = None
        if all_results:
            json_file, csv_file, pdf_file = save_results(all_results, keywords[0], watermark=watermark)

        # Send final summary
        yield f"data: {_json.dumps({'type': 'done', 'total': len(all_results), 'elapsed': f'{elapsed:.1f}s', 'platform_breakdown': platform_breakdown, 'json_file': json_file, 'csv_file': csv_file, 'pdf_file': pdf_file, 'results': [r.to_dict() for r in all_results]})}\n\n"

        if user and supabase_rest_configured():
            profile = user.get("profile", {})
            left = int(profile.get("daily_searches_left", 0))
            if left > 0:
                await decrement_search_limit(user["id"], left)

    return StreamingResponse(event_generator(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/profile")
async def profile(
    request: Request,
    username: str = Query(...),
    max: int | None = Query(None, ge=1, le=50),
    max_results: int | None = Query(None, ge=1, le=50),
    sort: str = Query("latest"),
    date_range: str = Query("all"),
):
    rate_limited = enforce_rate_limit(request, "profile")
    if rate_limited:
        return rate_limited

    user, plan, denial = await enforce_feature_access(request, "profile")
    if denial:
        return denial

    if user and plan:
      monthly_limit = int(plan.get("monthly_profile_limit", 0) or 0)
      if monthly_limit > 0:
        profile_data = user.get("profile", {})
        monthly_used = get_monthly_usage(user["id"], "profile", profile_data)
        if monthly_used >= monthly_limit:
          return JSONResponse(
            {
              "error": f"Kuota profil bulan ini habis ({monthly_limit}). Upgrade untuk lebih banyak.",
              "code": "profile_quota_exceeded",
              "limit": monthly_limit,
              "used": monthly_used,
              "upgrade_url": "/payment",
            },
            status_code=429,
          )

    max_value = max_results or max or 10

    cache_key = (username.lstrip("@").lower(), max_value, sort, date_range)
    cached = PROFILE_CACHE.get(cache_key)
    if cached and time.time() - cached[0] < PROFILE_CACHE_TTL_SECONDS:
      if user and plan:
        await increment_monthly_usage(user["id"], "profile")
        return {
            **cached[1],
            "cached": True,
            "elapsed": "<1s (cached)",
        }

    start = time.time()

    scraper = TikTokScraper()
    results = await scraper.scrape_profile(username, max_value, sort)
    results = [enrich_result_text(result) for result in results]
    if date_range != "all":
        results = filter_results_by_date_range(results, date_range)

    elapsed = time.time() - start
    json_file = csv_file = pdf_file = None
    if results:
        json_file, csv_file, pdf_file = save_results(results, f"profile_{username}")

    payload = {
        "username": username,
        "total": len(results),
        "elapsed": f"{elapsed:.1f}s",
        "cached": False,
        "plan_code": plan.get("code") if plan else None,
        "json_file": json_file,
        "csv_file": csv_file,
        "pdf_file": pdf_file,
        "results": [r.to_dict() for r in results],
    }
    PROFILE_CACHE[cache_key] = (time.time(), payload)
    
    if user and plan:
      await increment_monthly_usage(user["id"], "profile")
    return payload


def filter_results_by_date_range(results, date_range: str):
    days_lookup = {"7d": 7, "30d": 30}
    days = days_lookup.get(date_range)
    if not days:
        return results

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    filtered = []
    for result in results:
        parsed = parse_upload_date(result.upload_date)
        # Keep results with unparseable dates (don't drop them)
        if parsed is None or parsed >= cutoff:
            filtered.append(result)
    return filtered


def parse_upload_date(value: str | None):
    if not value:
        return None

    raw = str(value).strip()
    if not raw:
        return None

    if raw.isdigit():
        try:
            timestamp = int(raw)
            if timestamp > 10_000_000_000:
                timestamp = timestamp / 1000
            return datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None

    for parser in (datetime.fromisoformat,):
        try:
            parsed = parser(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            pass

    match = re.match(r"^(\d+)\s+(minute|hour|day|week|month|year)s?\s+ago$", raw.lower())
    if not match:
        return None

    amount = int(match.group(1))
    unit = match.group(2)
    if unit == "minute":
        delta = timedelta(minutes=amount)
    elif unit == "hour":
        delta = timedelta(hours=amount)
    elif unit == "day":
        delta = timedelta(days=amount)
    elif unit == "week":
        delta = timedelta(weeks=amount)
    elif unit == "month":
        delta = timedelta(days=30 * amount)
    else:
        delta = timedelta(days=365 * amount)
    return datetime.now(timezone.utc) - delta


@app.get("/api/comments")
async def comments(
    request: Request,
    url: str | None = Query(None),
    video_url: str | None = Query(None),
    platform: str | None = Query(None),
    max: int | None = Query(None, ge=1, le=200),
    max_comments: int | None = Query(None, ge=1, le=200),
):
    rate_limited = enforce_rate_limit(request, "comments")
    if rate_limited:
        return rate_limited

    user, plan, denial = await enforce_feature_access(request, "comments")
    if denial:
        return denial

    if user and plan:
      monthly_limit = int(plan.get("monthly_comment_limit", 0) or 0)
      if monthly_limit > 0:
        profile_data = user.get("profile", {})
        monthly_used = get_monthly_usage(user["id"], "comments", profile_data)
        if monthly_used >= monthly_limit:
          return JSONResponse(
            {
              "error": f"Kuota komentar bulan ini habis ({monthly_limit}). Upgrade untuk lebih banyak.",
              "code": "comments_quota_exceeded",
              "limit": monthly_limit,
              "used": monthly_used,
              "upgrade_url": "/payment",
            },
            status_code=429,
          )

    target_url = (url or video_url or "").strip()
    if not target_url:
        return JSONResponse({"error": "URL video wajib diisi via `url` atau `video_url`."}, 400)

    # Comments currently support TikTok only, but we accept the request
    detected_platform = (platform or "tiktok").lower()
    if detected_platform not in ("tiktok",):
        return JSONResponse({"error": "Fitur komentar saat ini baru mendukung TikTok. Platform lain segera hadir."}, 400)

    max_value = max_comments or max or 50

    cache_key = (target_url, max_value)
    cached = COMMENTS_CACHE.get(cache_key)
    if cached and time.time() - cached[0] < COMMENTS_CACHE_TTL_SECONDS:
      if user and plan:
        await increment_monthly_usage(user["id"], "comments")
        return {
            **cached[1],
            "cached": True,
        }

    scraper = TikTokScraper()
    result = await scraper.scrape_comments(target_url, max_value)
    video_comment_count = await scraper.get_video_comment_count(target_url)
    payload = {
        "url": target_url,
        "total": len(result),
        "video_comment_count": video_comment_count,
        "cached": False,
        "plan_code": plan.get("code") if plan else None,
        "comments": result,
    }
    COMMENTS_CACHE[cache_key] = (time.time(), payload)
    
    if user and plan:
      await increment_monthly_usage(user["id"], "comments")
    return payload


@app.get("/api/download")
async def download(file: str = Query(...)):
    requested = (file or "").strip()
    if not requested:
        return JSONResponse({"error": "Invalid file path"}, 400)

    # Clean the path and strip any leading 'output/' if present
    # because OUTPUT_DIR is already 'output'
    if requested.startswith("output/"):
        requested = requested[7:]

    candidate = Path(requested)
    if candidate.is_absolute() or ".." in candidate.parts:
        return JSONResponse({"error": "Invalid file path"}, 400)

    safe_path = (OUTPUT_DIR / candidate).resolve()
    if not str(safe_path).startswith(str(OUTPUT_DIR.resolve())):
        return JSONResponse({"error": "Invalid file path"}, 400)
    if not safe_path.exists() or not safe_path.is_file():
        return JSONResponse({"error": "File not found"}, 404)

    return FileResponse(str(safe_path), filename=safe_path.name)


# ── TikTok Shop product scraping endpoint ──────────────────
PRODUCT_CACHE: dict[str, tuple[float, list]] = {}
PRODUCT_CACHE_TTL = 3600  # 1 hour


def _kalodata_today_jakarta() -> str:
  return datetime.now(KALODATA_TIMEZONE).strftime("%Y-%m-%d")


def _kalodata_extract_list(payload: dict | list | None) -> list[dict]:
  if isinstance(payload, list):
    return [item for item in payload if isinstance(item, dict)]
  if isinstance(payload, dict):
    for key in ("list", "records", "items"):
      value = payload.get(key)
      if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
  return []


def _sanitize_product_payload(item: dict) -> dict:
  allowed_keys = {
    "product_id",
    "product_url",
    "name",
    "price",
    "original_price",
    "discount_pct",
    "sold_count",
    "rating",
    "review_count",
    "shop_name",
    "shop_url",
    "thumbnail",
    "commission_rate",
    "category",
    "video_url",
    "revenue",
    "seller_type",
    "ship_from",
    "items_sold_count",
  }
  return {key: value for key, value in item.items() if key in allowed_keys}


async def _fetch_kalodata_dashboard_intel(
  limit: int = 6,
  start_date: str | None = None,
  end_date: str | None = None,
  preset: str = "today",
) -> dict:
  scraper = KalodataScraper()
  start_value, end_value, window_label = _resolve_dashboard_window(preset=preset, start_date=start_date, end_date=end_date)
  try:
    client = await scraper._ensure_client()
    headers = scraper._api_headers()
    headers["Referer"] = scraper.BASE_URL + "/"

    async def fetch_list(path: str, payload: dict) -> list[dict]:
      response = await client.post(
        scraper.BASE_URL + path,
        json=payload,
        headers=headers,
      )
      data = response.json()
      if not data.get("success"):
        raise RuntimeError(data.get("message") or f"Kalodata error on {path}")
      return _kalodata_extract_list(data.get("data"))

    common_payload = {
      "country": KALODATA_COUNTRY,
      "page": 1,
      "size": limit,
      "startDate": start_value,
      "endDate": end_value,
    }
    products_by_revenue, products_by_sale, creators_by_revenue, shops_by_revenue = await asyncio.gather(
      fetch_list(
        "/product/queryList",
        {**common_payload, "sortColumn": "revenue", "sortDirection": "desc"},
      ),
      fetch_list(
        "/product/queryList",
        {**common_payload, "sortColumn": "sale", "sortDirection": "desc"},
      ),
      fetch_list(
        "/creator/queryList",
        {**common_payload, "sortColumn": "revenue", "sortDirection": "desc"},
      ),
      fetch_list(
        "/shop/queryList",
        {**common_payload, "sortColumn": "revenue", "sortDirection": "desc"},
      ),
    )

    note = ""
    if not products_by_revenue and not shops_by_revenue:
      note = "Data leaderboard produk dan shop untuk rentang tanggal ini belum tersedia. Data creator masih ditampilkan jika ada."

    return {
      "region": {
        "code": KALODATA_COUNTRY,
        "label": KALODATA_REGION_LABEL,
        "timezone": str(KALODATA_TIMEZONE),
      },
      "window": {
        "label": window_label,
        "start_date": start_value,
        "end_date": end_value,
      },
      "note": note,
      "summary": {
        "top_product_revenue": (products_by_revenue[0].get("revenue") if products_by_revenue else "Rp0.00"),
        "top_product_sale": (products_by_sale[0].get("sale") if products_by_sale else 0),
        "top_creator_revenue": (creators_by_revenue[0].get("revenue") if creators_by_revenue else "Rp0.00"),
        "top_shop_revenue": (shops_by_revenue[0].get("revenue") if shops_by_revenue else "Rp0.00"),
      },
      "products_by_revenue": products_by_revenue,
      "products_by_sale": products_by_sale,
      "creators_by_revenue": creators_by_revenue,
      "shops_by_revenue": shops_by_revenue,
    }
  finally:
    await scraper.close()


@app.get("/api/dashboard/trending")
async def get_dashboard_trending(
  request: Request,
  preset: str = Query("today"),
  start_date: str | None = Query(None),
  end_date: str | None = Query(None),
  limit: int = Query(6, ge=3, le=12),
):
  """Dashboard market intel for the selected Indonesia date range."""
  rate_limited = enforce_rate_limit(request, "dashboard")
  if rate_limited:
    return rate_limited

  user, plan, denial = await enforce_feature_access(request, "search")
  if denial:
    return denial

  plan_code = plan.get("code", "free") if plan else "free"
  if plan_code == "free":
    return JSONResponse(
      {
        "error": "Dashboard market intel hanya untuk pengguna berbayar.",
        "code": "upgrade_required",
        "upgrade_url": "/payment",
      },
      status_code=403,
    )

  start_value, end_value, _ = _resolve_dashboard_window(preset=preset, start_date=start_date, end_date=end_date)
  cache_key = (start_value, end_value, limit)
  cached = KALODATA_DASHBOARD_CACHE.get(cache_key)
  if cached and time.time() - cached[0] < KALODATA_DASHBOARD_CACHE_TTL_SECONDS:
    return {**cached[1], "cached": True}

  try:
    payload = await _fetch_kalodata_dashboard_intel(
      limit=limit,
      start_date=start_value,
      end_date=end_value,
      preset=preset,
    )
  except Exception as exc:
    print(f"[DashboardIntel] Error: {exc}")
    if cached:
      stale_payload = {
        **cached[1],
        "cached": True,
        "stale": True,
        "note": (cached[1].get("note") or "") + (" " if cached[1].get("note") else "") + "Data terbaru sedang belum bisa diambil, jadi dashboard menampilkan cache terakhir.",
      }
      return stale_payload
    return JSONResponse(
      {
        "error": f"Gagal memuat market intel Indonesia: {str(exc)[:140]}",
        "code": "market_intel_unavailable",
      },
      status_code=502,
    )

  KALODATA_DASHBOARD_CACHE[cache_key] = (time.time(), payload)
  return {**payload, "cached": False}

@app.get("/api/products")
async def get_video_products(
    request: Request,
    url: str = Query(..., description="TikTok video URL"),
):
    """Scrape affiliate product data from a TikTok video with keranjang kuning."""
    rate_limited = enforce_rate_limit(request, "search")
    if rate_limited:
        return rate_limited

    user, plan, denial = await enforce_feature_access(request, "search")
    if denial:
        return denial

    # Only allow paid tiers (product scraping is expensive)
    plan_code = plan.get("code", "free") if plan else "free"
    if plan_code == "free":
        return JSONResponse(
            {
                "error": "Fitur deteksi produk affiliate hanya untuk pengguna berbayar.",
                "code": "upgrade_required",
                "upgrade_url": "/payment",
            },
            status_code=403,
        )

    target = url.strip()
    if not target or "tiktok.com" not in target:
        return JSONResponse({"error": "URL TikTok video wajib diisi."}, 400)

    # Check cache
    cached = PRODUCT_CACHE.get(target)
    if cached and time.time() - cached[0] < PRODUCT_CACHE_TTL:
      cached_products = [_sanitize_product_payload(item) for item in cached[1]]
      return {
        "video_url": target,
        "total_products": len(cached_products),
        "products": cached_products,
        "cached": True,
      }

    proxy_url = os.getenv("PROXY_URL", "") or None
    scraper = TikTokShopScraper(proxy_url=proxy_url)

    try:
        products = await asyncio.wait_for(
            scraper.scrape_products_from_video(target),
            timeout=90,
        )
    except asyncio.TimeoutError:
        return JSONResponse(
            {"error": "Product scraping timed out. Coba lagi nanti."},
            status_code=504,
        )
    except Exception as e:
        print(f"[ProductAPI] Error: {e}")
        return JSONResponse(
            {"error": f"Gagal scrape produk: {str(e)[:100]}"},
            status_code=500,
        )

    product_dicts = [_sanitize_product_payload(p.to_dict()) for p in products]
    PRODUCT_CACHE[target] = (time.time(), product_dicts)

    return {
        "video_url": target,
        "total_products": len(product_dicts),
        "products": product_dicts,
        "cached": False,
    }


LANDING_HTML = """<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Sinyal - Content Intelligence untuk Creator Indonesia</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=DM+Serif+Display:ital@0;1&display=swap" rel="stylesheet">
<style>
:root {
  --bg: #faf3ec;
  --ink: #3b1a08;
  --soft: #705b4c;
  --muted: #9a8474;
  --line: rgba(84,52,29,0.08);
  --card: rgba(255,250,244,0.9);
  --accent: #c0391b;
  --accent-2: #ef5a29;
  --accent-light: rgba(192,57,27,0.08);
  --orange: #ef5a29;
  --orange-2: #ff8d42;
  --green: #285f58;
}
*{box-sizing:border-box;margin:0;padding:0;}
html{scroll-behavior:smooth;}
body{font-family:'Plus Jakarta Sans',sans-serif;color:var(--ink);background:var(--bg);-webkit-font-smoothing:antialiased;overflow-x:hidden;}
a{text-decoration:none;color:inherit;}

.wrap{width:min(1140px,100% - 40px);margin:0 auto;}
section{padding:80px 0;}

/* Nav */
nav{position:sticky;top:0;z-index:50;background:rgba(250,243,236,0.85);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);}
.nav-inner{display:flex;align-items:center;justify-content:space-between;height:64px;}
.brand{font-family:'DM Serif Display',serif;font-size:24px;letter-spacing:-0.03em;color:var(--accent);font-weight:400;}
.nav-links{display:flex;align-items:center;gap:32px;}
.nav-links a{font-size:14px;font-weight:500;color:var(--soft);transition:color .15s;}
.nav-links a:hover{color:var(--accent);}
.nav-cta .btn{padding:10px 22px;border-radius:999px;font-size:13px;}

/* Buttons */
.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;padding:14px 28px;border-radius:14px;font-weight:700;font-size:15px;border:none;cursor:pointer;transition:transform .18s,box-shadow .18s;}
.btn:hover{transform:translateY(-1px);}
.btn-primary{background:var(--accent);color:#fff;box-shadow:0 8px 28px rgba(192,57,27,0.2);}
.btn-primary:hover{box-shadow:0 14px 36px rgba(192,57,27,0.3);}
.btn-outline{background:transparent;border:1.5px solid var(--ink);color:var(--ink);}
.btn-outline:hover{border-color:var(--accent);color:var(--accent);}
.btn-nav{background:var(--accent);color:#fff;font-weight:700;box-shadow:0 4px 16px rgba(192,57,27,0.18);}

/* Hero */
.hero{padding:80px 0 0;text-align:center;position:relative;}
.hero-eyebrow{display:inline-flex;align-items:center;gap:8px;padding:8px 18px;border-radius:999px;background:rgba(192,57,27,0.06);border:1px solid rgba(192,57,27,0.1);font-size:12px;font-weight:700;color:var(--accent);letter-spacing:0.06em;text-transform:uppercase;margin-bottom:32px;}
.hero-eyebrow::before{content:"";width:8px;height:8px;border-radius:50%;background:var(--accent);}
.hero h1{font-family:'DM Serif Display',serif;font-size:clamp(52px,9vw,120px);line-height:0.95;letter-spacing:-0.04em;color:var(--ink);max-width:800px;margin:0 auto;}
.hero h1 em{font-style:italic;color:var(--accent);}
.hero-buttons{display:flex;justify-content:center;gap:14px;margin-top:40px;flex-wrap:wrap;}

/* Hero Visual */
.hero-visual{position:relative;margin-top:60px;min-height:420px;overflow:hidden;}
.hero-wave{position:absolute;bottom:60px;left:-10%;width:120%;pointer-events:none;}
.hero-wave svg{width:100%;height:auto;}

.float-card{position:absolute;background:#fff;border-radius:16px;box-shadow:0 12px 40px rgba(60,26,8,0.08);border:1px solid rgba(84,52,29,0.06);padding:14px 18px;font-size:13px;font-weight:700;animation:floaty 6s ease-in-out infinite;}
@keyframes floaty{0%,100%{transform:translateY(0)}50%{transform:translateY(-8px)}}

.float-hook{top:10px;right:15%;animation-delay:0s;display:flex;align-items:center;gap:10px;}
.float-hook-icon{width:32px;height:32px;border-radius:8px;background:#1a1a2e;display:flex;align-items:center;justify-content:center;}
.float-hook-icon svg{width:16px;height:16px;}
.float-hook-text{display:flex;flex-direction:column;gap:1px;}
.float-hook-label{font-size:9px;text-transform:uppercase;letter-spacing:0.1em;color:var(--muted);font-weight:800;}
.float-hook-val{font-size:13px;color:var(--ink);}

.float-sentiment{bottom:140px;left:8%;animation-delay:1s;display:flex;align-items:center;gap:10px;border-radius:999px;padding:12px 20px;}
.float-sentiment-heart{width:28px;height:28px;border-radius:50%;background:linear-gradient(135deg,var(--orange),var(--orange-2));display:flex;align-items:center;justify-content:center;color:#fff;font-size:12px;}
.float-sentiment-text{display:flex;flex-direction:column;}
.float-sentiment-num{font-size:20px;font-weight:800;color:var(--accent);letter-spacing:-0.02em;}
.float-sentiment-label{font-size:9px;text-transform:uppercase;letter-spacing:0.1em;color:var(--muted);font-weight:700;}

.float-dashboard{position:absolute;right:5%;bottom:0;width:min(520px,60%);background:#fff;border-radius:20px 20px 0 0;box-shadow:0 -8px 48px rgba(60,26,8,0.1);border:1px solid rgba(84,52,29,0.06);border-bottom:none;padding:24px 28px;animation:slideUp 0.8s ease-out;}
@keyframes slideUp{from{transform:translateY(40px);opacity:0}to{transform:translateY(0);opacity:1}}
.dash-header{display:flex;align-items:center;gap:10px;margin-bottom:18px;}
.dash-icon{width:36px;height:36px;border-radius:10px;background:linear-gradient(135deg,var(--orange),var(--orange-2));display:flex;align-items:center;justify-content:center;}
.dash-icon svg{width:18px;height:18px;fill:#fff;}
.dash-title{font-size:16px;font-weight:800;color:var(--ink);}
.dash-subtitle{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:0.08em;font-weight:700;}
.dash-body{display:grid;grid-template-columns:1.2fr 0.8fr;gap:14px;}
.dash-metric-card{background:rgba(250,243,236,0.6);border-radius:14px;padding:16px;position:relative;overflow:hidden;}
.dash-metric-label{font-size:11px;color:var(--muted);font-weight:700;margin-bottom:4px;}
.dash-metric-value{font-size:28px;font-weight:800;color:var(--accent);font-family:'DM Serif Display',serif;letter-spacing:-0.03em;}
.dash-metric-value .trend{font-size:14px;color:var(--green);margin-left:6px;}
.dash-chart{display:flex;align-items:flex-end;gap:6px;height:80px;margin-top:12px;}
.dash-bar{border-radius:6px 6px 0 0;flex:1;min-width:0;transition:height 0.5s ease;}
.dash-side{display:grid;gap:10px;}
.dash-score-card{background:rgba(250,243,236,0.6);border-radius:14px;padding:14px;text-align:center;}
.dash-score-label{font-size:9px;text-transform:uppercase;letter-spacing:0.1em;color:var(--muted);font-weight:800;margin-bottom:6px;}
.dash-score-value{font-size:32px;font-weight:900;font-family:'DM Serif Display',serif;color:var(--accent);}
.dash-node-card{background:rgba(250,243,236,0.6);border-radius:14px;padding:12px;display:flex;align-items:center;gap:8px;}
.dash-node-dots{display:flex;gap:4px;}
.dash-node-dots span{width:8px;height:8px;border-radius:50%;}
.dash-node-text{font-size:10px;text-transform:uppercase;letter-spacing:0.08em;color:var(--muted);font-weight:700;}

/* Trust */
.trust-strip{padding:48px 0;text-align:center;}
.trust-row{display:flex;align-items:center;justify-content:center;gap:48px;flex-wrap:wrap;}
.trust-item{display:flex;flex-direction:column;align-items:center;gap:4px;}
.trust-num{font-family:'DM Serif Display',serif;font-size:38px;color:var(--accent);letter-spacing:-0.03em;}
.trust-label{font-size:11px;color:var(--muted);font-weight:700;text-transform:uppercase;letter-spacing:0.08em;}

/* Features */
.feat-header{text-align:center;max-width:520px;margin:0 auto 56px;}
.feat-header h2{font-family:'DM Serif Display',serif;font-size:clamp(32px,4.5vw,52px);line-height:1.05;letter-spacing:-0.03em;}
.feat-header p{margin-top:14px;color:var(--soft);font-size:16px;line-height:1.7;}
.feat-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;}
.feat-card{padding:28px 24px;border-radius:20px;background:#fff;border:1px solid var(--line);transition:transform .2s,box-shadow .2s;}
.feat-card:hover{transform:translateY(-4px);box-shadow:0 16px 48px rgba(60,26,8,0.08);}
.feat-icon{width:44px;height:44px;border-radius:12px;display:flex;align-items:center;justify-content:center;margin-bottom:18px;box-shadow:0 6px 16px rgba(0,0,0,0.08);}
.feat-card h3{font-size:17px;font-weight:800;margin-bottom:6px;}
.feat-card p{color:var(--soft);font-size:14px;line-height:1.65;}
.feat-grid-sm{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-top:16px;}
.feat-sm{display:flex;align-items:center;gap:14px;padding:20px;border-radius:16px;background:#fff;border:1px solid var(--line);transition:transform .2s,box-shadow .2s;}
.feat-sm:hover{transform:translateY(-2px);box-shadow:0 8px 28px rgba(60,26,8,0.06);}
.feat-sm .feat-icon{width:38px;height:38px;border-radius:10px;margin-bottom:0;flex-shrink:0;}
.feat-sm h4{font-size:14px;font-weight:800;margin-bottom:2px;}
.feat-sm p{font-size:12px;color:var(--soft);line-height:1.5;}

/* Use Cases */
.usecase-header{margin-bottom:32px;}
.usecase-header h2{font-family:'DM Serif Display',serif;font-size:clamp(32px,4.5vw,52px);line-height:1.05;letter-spacing:-0.03em;}
.usecase-header p{margin-top:10px;color:var(--soft);font-size:16px;line-height:1.7;max-width:480px;}
.mode-tabs{display:flex;gap:8px;margin-bottom:20px;flex-wrap:wrap;}
.mode-tab{padding:10px 20px;border-radius:999px;border:1.5px solid var(--line);background:#fff;color:var(--soft);font-weight:700;font-size:13px;cursor:pointer;transition:all .15s;}
.mode-tab:hover{border-color:var(--green);color:var(--green);}
.mode-tab.active{background:var(--green);border-color:var(--green);color:#fff;}
.scenario-panel{display:none;grid-template-columns:1fr 1fr;gap:20px;padding:32px;border-radius:24px;background:#fff;border:1px solid var(--line);}
.scenario-panel.active{display:grid;}
.scenario-copy h3{font-size:22px;font-weight:800;margin-bottom:10px;line-height:1.2;}
.scenario-copy>p{color:var(--soft);line-height:1.7;font-size:15px;}
.scenario-points{margin-top:18px;display:grid;gap:8px;}
.scenario-point{padding:12px 14px;border-radius:14px;background:rgba(250,243,236,0.7);border:1px solid rgba(84,52,29,0.04);color:var(--soft);font-size:13px;line-height:1.6;font-weight:600;}
.scenario-shot{border-radius:18px;background:rgba(250,243,236,0.5);border:1px solid rgba(84,52,29,0.04);padding:22px;}
.shot-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;}
.shot-header strong{font-size:15px;}
.shot-label{font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:0.08em;padding:5px 12px;background:rgba(40,95,88,0.08);color:var(--green);border-radius:999px;}
.feed-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;}
.feed-card{border-radius:14px;padding:14px;background:linear-gradient(180deg,rgba(239,90,41,0.04),rgba(255,255,255,0.9));border:1px solid rgba(84,52,29,0.04);}
.feed-card strong{display:block;font-size:13px;margin-bottom:4px;}
.feed-card span{color:var(--muted);font-size:12px;line-height:1.5;}

/* Pricing */
.pricing-section{padding:80px 0;}
.pricing-wrap{padding:36px;border-radius:28px;background:#fff;border:1px solid var(--line);box-shadow:0 16px 56px rgba(60,26,8,0.06);}
.pricing-header{text-align:center;margin-bottom:40px;}
.pricing-header h2{font-family:'DM Serif Display',serif;font-size:clamp(32px,4.5vw,52px);line-height:1.05;letter-spacing:-0.03em;}
.pricing-header p{margin-top:10px;color:var(--soft);font-size:16px;}
.pricing-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;}
.price-card{padding:24px;border-radius:22px;background:rgba(250,243,236,0.5);border:1px solid var(--line);display:flex;flex-direction:column;transition:transform .2s,box-shadow .2s;}
.price-card:hover{transform:translateY(-4px);box-shadow:0 16px 48px rgba(60,26,8,0.08);}
.price-card.featured{background:linear-gradient(180deg,rgba(192,57,27,0.06),rgba(255,255,255,0.98));border-color:rgba(192,57,27,0.15);}
.badge{display:inline-flex;align-self:flex-start;padding:6px 14px;border-radius:999px;background:var(--accent-light);color:var(--accent);font-size:11px;font-weight:800;letter-spacing:0.02em;margin-bottom:16px;}
.price-card h3{font-size:22px;font-weight:800;margin-bottom:8px;}
.price{font-family:'DM Serif Display',serif;font-size:42px;letter-spacing:-0.04em;line-height:1;}
.price small{font-family:'Plus Jakarta Sans',sans-serif;font-size:14px;color:var(--muted);font-weight:600;}
.price-note{margin:10px 0 18px;color:var(--soft);font-size:13px;line-height:1.65;}
.price-list{display:grid;gap:10px;color:var(--soft);font-size:13px;line-height:1.6;flex:1;}
.price-list div{display:flex;align-items:center;gap:8px;}
.price-list div::before{content:"\2713";color:var(--accent);font-weight:800;flex-shrink:0;font-size:14px;}
.price-cta{margin-top:auto;padding-top:22px;}
.price-cta a{display:flex;align-items:center;justify-content:center;width:100%;padding:14px;border-radius:14px;color:#fff;font-weight:800;font-size:14px;background:linear-gradient(135deg,var(--orange),var(--orange-2));box-shadow:0 8px 24px rgba(239,90,41,0.18);transition:transform .15s,box-shadow .15s;}
.price-cta a:hover{transform:translateY(-1px);box-shadow:0 14px 36px rgba(239,90,41,0.28);}
.billing-toggle-wrap{display:flex;justify-content:center;margin:0 0 32px;}
.billing-toggle{display:flex;align-items:center;background:#fff;border:1.5px solid var(--line);border-radius:999px;padding:5px;gap:4px;box-shadow:0 2px 8px rgba(98,66,43,0.06);}
.billing-btn{padding:10px 24px;border-radius:999px;border:none;background:transparent;font-family:inherit;font-size:14px;font-weight:700;color:var(--muted);cursor:pointer;transition:all .2s;display:flex;align-items:center;gap:8px;}
.billing-btn.active{background:var(--ink);color:#fff;box-shadow:0 4px 12px rgba(0,0,0,0.12);}
.save-badge{padding:3px 10px;border-radius:999px;background:#16a34a;color:#fff;font-size:11px;font-weight:800;letter-spacing:0.02em;}
.price-annual{font-size:13px;color:var(--soft);margin-top:4px;height:18px;}
.popular-tag{position:absolute;top:-12px;left:24px;padding:6px 14px;border-radius:999px;background:linear-gradient(135deg,var(--accent),var(--orange-2));color:#fff;font-size:11px;font-weight:800;letter-spacing:0.02em;}
.price-card{position:relative;}

/* Affiliate */
.aff-section{padding:80px 0;}
.aff-wrap{padding:40px;border-radius:28px;background:linear-gradient(180deg,rgba(40,95,88,0.06),rgba(255,250,244,0.9));border:1px solid var(--line);box-shadow:var(--shadow-sm);}
.aff-header{text-align:center;max-width:560px;margin:0 auto 40px;}
.aff-header h2{font-family:'DM Serif Display',serif;font-size:clamp(32px,4vw,48px);line-height:1.05;letter-spacing:-0.03em;}
.aff-header p{margin-top:12px;color:var(--soft);font-size:16px;line-height:1.7;}
.aff-badge{display:inline-flex;align-items:center;gap:8px;padding:8px 16px;border-radius:999px;background:rgba(40,95,88,0.10);color:var(--green);font-size:12px;font-weight:800;letter-spacing:0.02em;margin-bottom:16px;}
.aff-badge::before{content:"";width:7px;height:7px;border-radius:50%;background:var(--green);}
.aff-steps{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:32px;}
.aff-step{padding:28px 24px;border-radius:20px;background:var(--card);border:1px solid var(--line);text-align:center;transition:transform .2s,box-shadow .2s;}
.aff-step:hover{transform:translateY(-3px);box-shadow:var(--shadow-md);}
.aff-step-num{width:44px;height:44px;border-radius:50%;background:linear-gradient(135deg,var(--green),#3a8a7f);color:#fff;font-weight:800;font-size:18px;display:flex;align-items:center;justify-content:center;margin:0 auto 14px;}
.aff-step h3{font-size:17px;font-weight:800;margin-bottom:6px;}
.aff-step p{color:var(--soft);font-size:14px;line-height:1.6;}
.aff-highlight{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;margin-bottom:32px;}
.aff-stat{padding:24px;border-radius:18px;background:rgba(255,255,255,0.8);border:1px solid var(--line);text-align:center;}
.aff-stat strong{display:block;font-family:'DM Serif Display',serif;font-size:40px;color:var(--accent);letter-spacing:-0.03em;}
.aff-stat span{color:var(--soft);font-size:13px;font-weight:600;}
.aff-perks{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:32px;}
.aff-perk{display:flex;align-items:flex-start;gap:12px;padding:16px 18px;border-radius:14px;background:rgba(255,255,255,0.7);border:1px solid rgba(84,52,29,0.06);}
.aff-perk-icon{flex-shrink:0;width:32px;height:32px;border-radius:10px;background:rgba(40,95,88,0.10);display:flex;align-items:center;justify-content:center;font-size:16px;}
.aff-perk h4{font-size:14px;font-weight:800;margin-bottom:2px;}
.aff-perk p{color:var(--soft);font-size:13px;line-height:1.5;}
.aff-cta-row{display:flex;align-items:center;justify-content:center;gap:12px;flex-wrap:wrap;}
@media(max-width:960px){.aff-steps{grid-template-columns:1fr;}.aff-highlight{grid-template-columns:1fr;}.aff-perks{grid-template-columns:1fr;}}

/* CTA */
.final-cta{padding:36px;border-radius:28px;background:linear-gradient(135deg,rgba(192,57,27,0.08),rgba(40,95,88,0.06));border:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;gap:24px;flex-wrap:wrap;}
.final-cta h2{font-family:'DM Serif Display',serif;font-size:clamp(28px,3.5vw,42px);line-height:1.05;letter-spacing:-0.03em;margin-bottom:8px;}
.final-cta p{color:var(--soft);font-size:15px;line-height:1.7;max-width:480px;}

/* Footer */
footer{padding:40px 0 48px;border-top:1px solid var(--line);}
.footer-inner{display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:16px;}
.footer-brand{font-family:'DM Serif Display',serif;font-size:22px;color:var(--accent);letter-spacing:-0.03em;}
.footer-sub{font-size:12px;color:var(--muted);margin-top:4px;}
.footer-links{display:flex;gap:24px;}
.footer-links a{font-size:13px;color:var(--soft);font-weight:600;transition:color .15s;}
.footer-links a:hover{color:var(--accent);}

.hamburger{display:none;background:none;border:none;cursor:pointer;padding:8px;flex-direction:column;gap:5px;}
.hamburger span{display:block;width:22px;height:2px;background:var(--ink);border-radius:2px;}

@media(max-width:1100px){.pricing-grid{grid-template-columns:repeat(2,1fr);}.float-dashboard{width:min(420px,55%);}}
@media(max-width:960px){.hero h1{font-size:clamp(44px,8vw,72px);}.scenario-panel,.feat-grid{grid-template-columns:1fr;}.feat-grid-sm{grid-template-columns:1fr;}.float-dashboard{position:relative;right:auto;bottom:auto;width:100%;border-radius:20px;margin:40px auto 0;max-width:520px;border:1px solid var(--line);}.float-hook,.float-sentiment{display:none;}.hero-visual{min-height:auto;overflow:visible;}.hero-wave{display:none;}}
@media(max-width:720px){section{padding:56px 0;}.hero{padding:48px 0 0;}.hero h1{font-size:42px;line-height:1.02;}.pricing-grid{grid-template-columns:1fr;}.feed-grid{grid-template-columns:1fr;}.final-cta{flex-direction:column;align-items:flex-start;}.trust-row{gap:24px;}.trust-num{font-size:30px;}.nav-links{display:none;position:absolute;top:64px;left:0;right:0;flex-direction:column;background:rgba(250,243,236,0.98);backdrop-filter:blur(16px);padding:20px;border-bottom:1px solid var(--line);gap:12px;}.nav-links.open{display:flex;}.nav-cta{display:none;}.hamburger{display:flex !important;}.dash-body{grid-template-columns:1fr;}}
</style>
</head>
<body>

<nav>
  <div class="wrap nav-inner">
    <a href="/" class="brand">Sinyal</a>
    <button class="hamburger" onclick="document.querySelector('.nav-links').classList.toggle('open')" aria-label="Menu"><span></span><span></span><span></span></button>
    <div class="nav-links">
      <a href="#fitur">Product</a>
      <a href="#pakai">Trends</a>
      <a href="#harga">Pricing</a>
      <a href="#affiliate">Affiliate</a>
    </div>
    <div class="nav-cta"><a class="btn btn-nav" href="/signup">Get Started</a></div>
  </div>
</nav>

<header class="hero">
  <div class="wrap">
    <div class="hero-eyebrow">The Pulse of Content</div>
    <h1>Tangkapi<br><em>Sinyal</em><br>Yang Lagi<br>Rame.</h1>
    <div class="hero-buttons">
      <a class="btn btn-primary" href="/signup">Mulai Analisis</a>
      <a class="btn btn-outline" href="#fitur">Lihat Demo</a>
    </div>
  </div>
  <div class="hero-visual">
    <div class="hero-wave">
      <svg viewBox="0 0 1440 320" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M0,224 C180,280 360,160 540,180 C720,200 900,280 1080,240 C1260,200 1380,160 1440,180 L1440,320 L0,320 Z" fill="rgba(192,57,27,0.04)" stroke="rgba(192,57,27,0.1)" stroke-width="1.5" stroke-dasharray="8 6"/>
      </svg>
    </div>
    <div class="float-card float-hook">
      <div class="float-hook-icon"><svg viewBox="0 0 24 24" fill="white"><path d="M12 3v10.55A4 4 0 1014 17V7h4V3h-6z"/></svg></div>
      <div class="float-hook-text"><span class="float-hook-label">Viral Hook</span><span class="float-hook-val">"Rahasia yang mereka..."</span></div>
    </div>
    <div class="float-card float-sentiment">
      <div class="float-sentiment-heart">&#9829;</div>
      <div class="float-sentiment-text"><span class="float-sentiment-num">1.2M</span><span class="float-sentiment-label">Sentiment Score</span></div>
    </div>
    <div class="float-dashboard">
      <div class="dash-header">
        <div class="dash-icon"><svg viewBox="0 0 24 24"><path d="M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm0 16H5V5h14v14zM7 10h2v7H7zm4-3h2v10h-2zm4 6h2v4h-2z"/></svg></div>
        <div><div class="dash-title">Real-time Signals</div><div class="dash-subtitle">Global Trend Monitoring</div></div>
      </div>
      <div class="dash-body">
        <div class="dash-metric-card">
          <div class="dash-metric-label">Engagement Velocity</div>
          <div class="dash-metric-value">+248.4%<span class="trend">&#8599;</span></div>
          <div class="dash-chart" id="heroChart"></div>
        </div>
        <div class="dash-side">
          <div class="dash-score-card"><div class="dash-score-label">Virality Score</div><div class="dash-score-value">A+</div></div>
          <div class="dash-node-card"><div class="dash-node-dots"><span style="background:var(--accent);"></span><span style="background:var(--orange);"></span><span style="background:var(--muted);"></span></div><span class="dash-node-text">Node Synced</span></div>
        </div>
      </div>
    </div>
  </div>
</header>

<div class="trust-strip">
  <div class="wrap trust-row">
    <div class="trust-item"><div class="trust-num">5</div><div class="trust-label">Platform</div></div>
    <div class="trust-item"><div class="trust-num">&lt;30<span style="font-size:16px">dtk</span></div><div class="trust-label">Waktu Scan</div></div>
    <div class="trust-item"><div class="trust-num">1</div><div class="trust-label">Dashboard</div></div>
    <div class="trust-item"><div class="trust-num">&infin;</div><div class="trust-label">Insight</div></div>
  </div>
</div>

<section id="fitur">
  <div class="wrap">
    <div class="feat-header"><h2>Semua yang kamu butuhkan</h2><p>Satu dashboard buat riset konten, analisis creator, dan baca pasar.</p></div>
    <div class="feat-grid">
      <div class="feat-card">
        <div class="feat-icon" style="background:linear-gradient(135deg,var(--orange),var(--orange-2));"><svg width="20" height="20" fill="#fff" viewBox="0 0 24 24"><path d="M15.5 14h-.79l-.28-.27a6.5 6.5 0 001.48-5.34C15.41 5.44 13 3.5 10 3.5S4.59 5.44 4.09 8.39A6.5 6.5 0 0010.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L21.49 19l-4.99-5zm-5 0C8.01 14 6 11.99 6 9.5S8.01 5 10.5 5 15 7.01 15 9.5 12.99 14 10.5 14z"/></svg></div>
        <h3>Riset Keyword</h3><p>Cari topik lintas TikTok, IG, YouTube, X, Facebook. Filter views, likes, tanggal.</p>
      </div>
      <div class="feat-card">
        <div class="feat-icon" style="background:linear-gradient(135deg,var(--green),#3a8a7f);"><svg width="20" height="20" fill="#fff" viewBox="0 0 24 24"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm0 14H5.17L4 17.17V4h16v12z"/><path d="M7 9h2v2H7zm4 0h2v2h-2zm4 0h2v2h-2z"/></svg></div>
        <h3>Komentar Intel</h3><p>Ekstrak komentar video. Lihat bahasa pasar, keluhan, pujian, dan pain point.</p>
      </div>
      <div class="feat-card">
        <div class="feat-icon" style="background:linear-gradient(135deg,#1a1a2e,#16213e);"><svg width="20" height="20" fill="#fff" viewBox="0 0 24 24"><path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z"/></svg></div>
        <h3>Profil Creator</h3><p>Analisis feed creator. Rata-rata views, engagement, dan pola konten.</p>
      </div>
    </div>
    <div class="feat-grid-sm">
      <div class="feat-sm"><div class="feat-icon" style="background:linear-gradient(135deg,#7c3aed,#a855f7);"><svg width="18" height="18" fill="#fff" viewBox="0 0 24 24"><path d="M14 2H6c-1.1 0-2 .9-2 2v16c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V8l-6-6zM6 20V4h7v5h5v11H6z"/></svg></div><div><h4>Transkrip Otomatis</h4><p>Baca isi video tanpa nonton.</p></div></div>
      <div class="feat-sm"><div class="feat-icon" style="background:linear-gradient(135deg,#059669,#10b981);"><svg width="18" height="18" fill="#fff" viewBox="0 0 24 24"><path d="M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z"/></svg></div><div><h4>Export JSON & CSV</h4><p>Download data riset langsung.</p></div></div>
      <div class="feat-sm"><div class="feat-icon" style="background:linear-gradient(135deg,#d97706,#f59e0b);"><svg width="18" height="18" fill="#fff" viewBox="0 0 24 24"><path d="M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm0 16H5V5h14v14zM7 10h2v7H7zm4-3h2v10h-2zm4 6h2v4h-2z"/></svg></div><div><h4>Hook & CTA Analysis</h4><p>Nilai kekuatan hook dan call-to-action.</p></div></div>
    </div>
  </div>
</section>

<section id="pakai">
  <div class="wrap">
    <div class="usecase-header"><h2>Pakai sesuai cara kerja kamu</h2><p>Sama alatnya, beda cara pakainya.</p></div>
    <div class="mode-tabs">
      <button class="mode-tab active" type="button" data-mode="umkm">UMKM</button>
      <button class="mode-tab" type="button" data-mode="agency">Agency</button>
      <button class="mode-tab" type="button" data-mode="creator">Creator Scout</button>
    </div>
    <div class="scenario-panel active" data-panel="umkm">
      <div class="scenario-copy"><h3>&#127978; Cari ide konten sebelum posting</h3><p>Topik apa yang hidup, angle kompetitor, dan komentar paling sering muncul.</p><div class="scenario-points"><div class="scenario-point">&#9749; Cari topik seperti "kopi kekinian", "serum jerawat"</div><div class="scenario-point">&#128200; Lihat konten paling ramai dalam 7-30 hari</div><div class="scenario-point">&#128172; Ambil bahasa komentar buat bahan caption</div></div></div>
      <div class="scenario-shot"><div class="shot-header"><strong>Contoh hasil</strong><span class="shot-label">UMKM</span></div><div class="feed-grid"><div class="feed-card"><strong>Topik ramai</strong><span>"Serum barrier repair" naik karena komentar soal iritasi.</span></div><div class="feed-card"><strong>Format menang</strong><span>Video 20-30 detik dengan hook masalah nyata.</span></div><div class="feed-card"><strong>Komentar dominan</strong><span>"Buat kulit sensitif aman ga?" paling sering muncul.</span></div><div class="feed-card"><strong>Arah konten</strong><span>Lanjut ke edukasi, testimoni, atau perbandingan.</span></div></div></div>
    </div>
    <div class="scenario-panel" data-panel="agency">
      <div class="scenario-copy"><h3>&#128203; Riset cepat buat pitch & report</h3><p>Siapa yang layak dipantau, topik mana yang naik.</p><div class="scenario-points"><div class="scenario-point">&#128202; Bandingkan creator dari engagement & postingan</div><div class="scenario-point">&#128172; Tarik komentar buat cari pain point</div><div class="scenario-point">&#127919; Filter views & tanggal untuk report bersih</div></div></div>
      <div class="scenario-shot"><div class="shot-header"><strong>Contoh hasil</strong><span class="shot-label">Agency</span></div><div class="feed-grid"><div class="feed-card"><strong>Shortlist creator</strong><span>5 akun naik karena performa stabil.</span></div><div class="feed-card"><strong>Sinyal promosi</strong><span>Konten sponsor bisa dipisah dari organik.</span></div><div class="feed-card"><strong>Ringkasan cepat</strong><span>Views, likes, komentar dalam satu alur.</span></div><div class="feed-card"><strong>Waktu hemat</strong><span>Riset 1-2 jam dipotong lebih cepat.</span></div></div></div>
    </div>
    <div class="scenario-panel" data-panel="creator">
      <div class="scenario-copy"><h3>&#127919; Cari creator yang beneran cocok</h3><p>Bukan cuma followers besar. Lihat komentar, gaya bahasa, dan seberapa natural kontennya.</p><div class="scenario-points"><div class="scenario-point">&#128100; Buka profil creator & lihat performa</div><div class="scenario-point">&#128172; Baca komentar — cek kualitas interaksi</div><div class="scenario-point">&#128221; Transkrip buat screening cepat</div></div></div>
      <div class="scenario-shot"><div class="shot-header"><strong>Contoh hasil</strong><span class="shot-label">Creator Scout</span></div><div class="feed-grid"><div class="feed-card"><strong>Engagement stabil</strong><span>Followers sedang tapi komentar hidup.</span></div><div class="feed-card"><strong>Tone cocok</strong><span>Bahasa nyambung untuk brand lokal.</span></div><div class="feed-card"><strong>Risiko sponsor</strong><span>Terlalu sering promosi terlihat dari pola.</span></div><div class="feed-card"><strong>Screening cepat</strong><span>Transkrip bantu saring tanpa nonton.</span></div></div></div>
    </div>
  </div>
</section>

<section id="harga" class="pricing-section">
  <div class="wrap">
    <div class="pricing-wrap">
      <div class="pricing-header">
        <h2>Harga yang masih masuk akal</h2>
        <p>Mulai gratis, upgrade kalau butuh lebih. Bayar bulanan atau hemat dengan tahunan.</p>
      </div>
      <div class="billing-toggle-wrap">
        <div class="billing-toggle">
          <button class="billing-btn active" data-billing="monthly" type="button">Bulanan</button>
          <button class="billing-btn" data-billing="yearly" type="button">Tahunan <span class="save-badge">Hemat ~35%</span></button>
        </div>
      </div>
      <div class="pricing-grid">
        <div class="price-card">
          <div class="badge">Gratis selamanya</div>
          <h3>Free</h3>
          <div class="price-note">Mulai riset konten viral tanpa biaya.</div>
          <div class="price">Rp0 <small>/ selamanya</small></div>
          <div class="price-annual">&nbsp;</div>
          <div class="price-list"><div>3 pencarian per hari</div><div>TikTok saja</div><div>Daily Briefing (preview)</div><div>1 Content Autopsy / minggu</div></div>
          <div class="price-cta"><a href="/signup">Mulai Gratis</a></div>
        </div>
        <div class="price-card" style="border-color:rgba(40,95,88,0.2);">
          <div class="badge" style="background:rgba(40,95,88,0.1);color:#285f58;">Paling Laris</div>
          <h3>Paket 7 Hari</h3>
          <div class="price-note">Akses penuh selama 7 hari. Tanpa auto-renew, tanpa ribet.</div>
          <div class="price">Rp29rb <small>/ 7 hari</small></div>
          <div class="price-annual"><span style="color:var(--green);font-weight:700;">Sekali bayar, langsung aktif</span></div>
          <div class="price-list"><div>Unlimited pencarian 7 hari</div><div>TikTok + IG + YouTube</div><div>10 analisa konten AI</div><div>Export tanpa watermark</div></div>
          <div class="price-cta"><a href="/checkout/weekly">Beli Paket 7 Hari</a></div>
        </div>
        <div class="price-card">
          <div class="badge" style="background:rgba(59,130,246,0.1);color:#2563eb;">Fleksibel</div>
          <h3>Paket 50 Kredit</h3>
          <div class="price-note">Beli sekali, pakai kapan saja. Tidak hangus.</div>
          <div class="price">Rp49rb <small>/ 50 kredit</small></div>
          <div class="price-annual"><span style="color:#2563eb;font-weight:700;">1 search = 1 kredit</span></div>
          <div class="price-list"><div>50 kredit fleksibel</div><div>TikTok + IG + YouTube</div><div>Profil &amp; komentar masing-masing 1 kredit</div><div>Analisa konten AI termasuk</div></div>
          <div class="price-cta"><a href="/checkout/credit">Beli 50 Kredit</a></div>
        </div>
        <div class="price-card">
          <div class="badge">Mulai serius</div>
          <h3>Starter</h3>
          <div class="price-note">Untuk solopreneur yang mau insight cepat tanpa ribet riset manual.</div>
          <div class="price"><span data-monthly="Rp49rb" data-yearly="Rp32rb">Rp49rb</span> <small data-monthly="/ bulan" data-yearly="/ bulan">/ bulan</small></div>
          <div class="price-annual" data-monthly="&nbsp;" data-yearly="Rp389rb / tahun">&nbsp;</div>
          <div class="price-list"><div>30 pencarian per hari</div><div>TikTok + Instagram</div><div>Full Daily Briefing</div><div>100 insight / bulan</div><div>10 Content Autopsy / bulan</div><div>1 Niche Playbook</div></div>
          <div class="price-cta"><a href="/payment">Ambil Starter</a></div>
        </div>
        <div class="price-card featured">
          <div class="popular-tag">Most Popular</div>
          <h3>Pro</h3>
          <div class="price-note">Akses penuh ke semua platform dengan insight yang siap dipakai tim.</div>
          <div class="price"><span data-monthly="Rp99rb" data-yearly="Rp65rb">Rp99rb</span> <small data-monthly="/ bulan" data-yearly="/ bulan">/ bulan</small></div>
          <div class="price-annual" data-monthly="&nbsp;" data-yearly="Rp789rb / tahun">&nbsp;</div>
          <div class="price-list"><div>Pencarian unlimited</div><div>Semua platform (TikTok, IG, YT, X, FB)</div><div>Full Daily Briefing</div><div>Unlimited Content Autopsy</div><div>Semua Niche Playbook</div><div>Hook, CTA &amp; angle analysis</div></div>
          <div class="price-cta"><a href="/payment">Upgrade ke Pro</a></div>
        </div>
      </div>
    </div>
  </div>
</section>

<section id="affiliate" class="aff-section">
  <div class="wrap">
    <div class="aff-wrap">
      <div class="aff-header">
        <div class="aff-badge">Program Affiliate</div>
        <h2>Dapet duit sambil share tools.</h2>
        <p>Jadi affiliate Sinyal, share link kamu, dan dapat <strong>20% komisi</strong> dari setiap orang yang upgrade lewat link kamu. Recurring selama mereka bayar.</p>
      </div>

      <div class="aff-steps">
        <div class="aff-step">
          <div class="aff-step-num">1</div>
          <h3>Daftar &amp; Aktifkan</h3>
          <p>Buat akun gratis, buka tab Affiliate di dashboard, dan aktifkan link referral kamu dalam 1 klik.</p>
        </div>
        <div class="aff-step">
          <div class="aff-step-num">2</div>
          <h3>Share Link Kamu</h3>
          <p>Bagikan link referral unik kamu ke teman, followers, atau di konten kamu. Mereka daftar lewat link itu.</p>
        </div>
        <div class="aff-step">
          <div class="aff-step-num">3</div>
          <h3>Dapat 20% Komisi</h3>
          <p>Setiap kali referral kamu upgrade ke plan berbayar, kamu langsung dapat 20% dari pembayaran mereka.</p>
        </div>
      </div>

      <div class="aff-highlight">
        <div class="aff-stat"><strong>20%</strong><span>Komisi per transaksi</span></div>
        <div class="aff-stat"><strong>Rp50rb</strong><span>Minimum payout</span></div>
        <div class="aff-stat"><strong>&infin;</strong><span>Tanpa batas referral</span></div>
      </div>

      <div class="aff-perks">
        <div class="aff-perk">
          <div class="aff-perk-icon">&#128176;</div>
          <div><h4>Komisi Recurring</h4><p>Selama referral kamu tetap bayar langganan, kamu tetap dapat komisi setiap bulan.</p></div>
        </div>
        <div class="aff-perk">
          <div class="aff-perk-icon">&#9889;</div>
          <div><h4>Dashboard Real-time</h4><p>Pantau klik, signup, konversi, dan saldo kamu langsung dari dashboard affiliate.</p></div>
        </div>
        <div class="aff-perk">
          <div class="aff-perk-icon">&#128179;</div>
          <div><h4>Payout Fleksibel</h4><p>Withdraw via transfer bank atau e-wallet (GoPay, OVO, DANA). Proses cepat.</p></div>
        </div>
        <div class="aff-perk">
          <div class="aff-perk-icon">&#127775;</div>
          <div><h4>Gratis Ikut</h4><p>Tidak perlu bayar apa-apa. Bahkan akun Free pun bisa jadi affiliate.</p></div>
        </div>
      </div>

      <div class="aff-cta-row">
        <a class="btn btn-primary btn-lg" href="/signup?ref=affiliate">Gabung Affiliate &rarr;</a>
        <a class="btn btn-ghost btn-lg" href="/affiliate">Pelajari Selengkapnya</a>
      </div>
    </div>
  </div>
</section>

<section>
  <div class="wrap final-cta">
    <div><h2>Masuk, ketik topik, lihat sendiri.</h2><p>Daftar gratis. Langsung coba. Upgrade kalau cocok.</p></div>
    <a class="btn btn-primary" href="/signup">Coba sekarang &rarr;</a>
  </div>
</section>

<footer>
  <div class="wrap footer-inner">
    <div><div class="footer-brand">Sinyal</div><div class="footer-sub">&copy; 2026 Sinyal Editorial. Predictive Content Intelligence.</div></div>
    <div class="footer-links"><a href="/signup">Daftar</a><a href="/signin">Masuk</a><a href="/payment">Pricing</a><a href="/affiliate">Affiliate</a><a href="#fitur">Product</a></div>
  </div>
</footer>

<script>
(function(){
  var c=document.getElementById('heroChart');if(!c)return;
  var h=[35,25,42,55,38,65,50,78];
  var cl=['rgba(192,57,27,0.15)','rgba(192,57,27,0.12)','rgba(192,57,27,0.18)','rgba(239,90,41,0.25)','rgba(192,57,27,0.15)','rgba(239,90,41,0.5)','rgba(239,90,41,0.35)','rgba(239,90,41,0.7)'];
  h.forEach(function(v,i){var b=document.createElement('div');b.className='dash-bar';b.style.background=cl[i];b.style.height='0%';c.appendChild(b);setTimeout(function(){b.style.height=v+'%'},200+i*80)});
})();
document.querySelectorAll(".mode-tab").forEach(function(btn){btn.addEventListener("click",function(){document.querySelectorAll(".mode-tab").forEach(function(b){b.classList.remove("active")});document.querySelectorAll(".scenario-panel").forEach(function(p){p.classList.remove("active")});btn.classList.add("active");var p=document.querySelector('[data-panel="'+btn.dataset.mode+'"]');if(p)p.classList.add("active")})});
document.querySelectorAll('a[href^="#"]').forEach(function(a){a.addEventListener("click",function(e){var t=document.querySelector(a.getAttribute("href"));if(t){e.preventDefault();t.scrollIntoView({behavior:"smooth",block:"start"})}})});
/* Billing toggle */
document.querySelectorAll('.billing-btn').forEach(function(btn){btn.addEventListener('click',function(){document.querySelectorAll('.billing-btn').forEach(function(b){b.classList.remove('active')});btn.classList.add('active');var billing=btn.dataset.billing;document.querySelectorAll('.price span[data-monthly]').forEach(function(el){el.textContent=el.dataset[billing]});document.querySelectorAll('.price small[data-monthly]').forEach(function(el){el.textContent=el.dataset[billing]});document.querySelectorAll('.price-annual[data-monthly]').forEach(function(el){el.innerHTML=el.dataset[billing]})})});
</script>
</body>
</html>"""


AFFILIATE_HTML = """<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Sinyal Affiliate Program - Dapatkan 20% Komisi</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=DM+Serif+Display:ital@0;1&display=swap" rel="stylesheet">
<style>
:root{--bg:#faf3ec;--ink:#3b1a08;--soft:#705b4c;--muted:#9a8474;--line:rgba(84,52,29,0.08);--card:rgba(255,250,244,0.9);--accent:#c0391b;--accent-2:#ef5a29;--orange:#ef5a29;--orange-2:#ff8d42;--green:#285f58;--green-soft:rgba(40,95,88,0.10);}
*{box-sizing:border-box;margin:0;padding:0;}
html{scroll-behavior:smooth;}
body{font-family:'Plus Jakarta Sans',sans-serif;color:var(--ink);background:linear-gradient(180deg,#fffaf4 0%,#faf3ec 60%,#f1e5d8 100%);-webkit-font-smoothing:antialiased;}
a{text-decoration:none;color:inherit;}
.wrap{width:min(1120px,100% - 40px);margin:0 auto;}
section{padding:72px 0;}

/* Nav */
nav{position:sticky;top:0;z-index:50;background:rgba(250,243,236,0.82);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);border-bottom:1px solid var(--line);}
.nav-inner{display:flex;align-items:center;justify-content:space-between;height:64px;}
.brand{font-family:'DM Serif Display',serif;font-size:26px;letter-spacing:-0.04em;color:var(--ink);}
.brand span{color:var(--accent);}
.nav-cta{display:flex;gap:8px;}

/* Buttons */
.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;padding:12px 22px;border-radius:12px;font-weight:800;font-size:14px;border:none;cursor:pointer;transition:transform .15s,box-shadow .15s;}
.btn:hover{transform:translateY(-1px);}
.btn-primary{background:linear-gradient(135deg,var(--accent),var(--accent-2));color:#fff;box-shadow:0 8px 24px rgba(192,57,27,0.18);}
.btn-primary:hover{box-shadow:0 12px 32px rgba(192,57,27,0.28);}
.btn-green{background:linear-gradient(135deg,var(--green),#3a8a7f);color:#fff;box-shadow:0 8px 24px rgba(40,95,88,0.18);}
.btn-green:hover{box-shadow:0 12px 32px rgba(40,95,88,0.28);}
.btn-ghost{background:rgba(255,255,255,0.7);border:1.5px solid var(--line);color:var(--ink);}
.btn-ghost:hover{border-color:var(--accent);color:var(--accent);}
.btn-lg{padding:16px 28px;font-size:15px;border-radius:14px;}

/* Hero */
.aff-hero{padding:80px 0 60px;text-align:center;}
.aff-hero-badge{display:inline-flex;align-items:center;gap:8px;padding:8px 16px;border-radius:999px;background:var(--green-soft);color:var(--green);font-size:12px;font-weight:800;letter-spacing:0.02em;margin-bottom:20px;}
.aff-hero-badge::before{content:"";width:7px;height:7px;border-radius:50%;background:var(--green);}
.aff-hero h1{font-family:'DM Serif Display',serif;font-size:clamp(40px,5.5vw,64px);line-height:1.05;letter-spacing:-0.04em;margin-bottom:16px;}
.aff-hero h1 em{font-style:italic;color:var(--green);}
.aff-hero p{max-width:560px;margin:0 auto 28px;color:var(--soft);font-size:17px;line-height:1.7;}
.aff-hero-actions{display:flex;justify-content:center;gap:12px;flex-wrap:wrap;}

/* Stats */
.aff-social{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;padding:48px 0;border-top:1px solid var(--line);border-bottom:1px solid var(--line);}
.aff-social-item{text-align:center;}
.aff-social-num{font-family:'DM Serif Display',serif;font-size:36px;color:var(--accent);letter-spacing:-0.03em;}
.aff-social-label{font-size:12px;color:var(--muted);font-weight:700;text-transform:uppercase;letter-spacing:0.06em;}

/* How it works */
.how-header{text-align:center;max-width:500px;margin:0 auto 40px;}
.how-header h2{font-family:'DM Serif Display',serif;font-size:clamp(32px,4vw,48px);line-height:1.05;letter-spacing:-0.03em;}
.how-header p{margin-top:10px;color:var(--soft);font-size:16px;line-height:1.7;}
.how-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:20px;margin-bottom:48px;}
.how-card{padding:32px 24px;border-radius:22px;background:var(--card);border:1px solid var(--line);text-align:center;transition:transform .2s,box-shadow .2s;}
.how-card:hover{transform:translateY(-3px);box-shadow:0 12px 40px rgba(98,66,43,0.08);}
.how-num{width:52px;height:52px;border-radius:50%;background:linear-gradient(135deg,var(--green),#3a8a7f);color:#fff;font-weight:800;font-size:22px;display:flex;align-items:center;justify-content:center;margin:0 auto 16px;font-family:'DM Serif Display',serif;}
.how-card h3{font-size:18px;font-weight:800;margin-bottom:8px;}
.how-card p{color:var(--soft);font-size:14px;line-height:1.65;}

/* Benefits */
.ben-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:48px;}
.ben-card{display:flex;align-items:flex-start;gap:16px;padding:24px;border-radius:20px;background:var(--card);border:1px solid var(--line);transition:transform .2s;}
.ben-card:hover{transform:translateY(-2px);}
.ben-icon{flex-shrink:0;width:44px;height:44px;border-radius:12px;background:var(--green-soft);display:flex;align-items:center;justify-content:center;font-size:20px;}
.ben-card h3{font-size:16px;font-weight:800;margin-bottom:4px;}
.ben-card p{color:var(--soft);font-size:14px;line-height:1.6;}

/* FAQ */
.faq-header{text-align:center;margin-bottom:32px;}
.faq-header h2{font-family:'DM Serif Display',serif;font-size:clamp(28px,3.5vw,40px);line-height:1.05;letter-spacing:-0.03em;}
.faq-list{max-width:720px;margin:0 auto;display:grid;gap:12px;}
.faq-item{padding:20px 24px;border-radius:16px;background:var(--card);border:1px solid var(--line);}
.faq-item summary{cursor:pointer;font-weight:800;font-size:15px;list-style:none;display:flex;align-items:center;justify-content:space-between;gap:12px;}
.faq-item summary::after{content:"+";font-size:20px;color:var(--muted);transition:transform .2s;}
.faq-item[open] summary::after{transform:rotate(45deg);color:var(--accent);}
.faq-item p{margin-top:12px;color:var(--soft);font-size:14px;line-height:1.7;}

/* Calculator */
.calc-wrap{padding:32px;border-radius:24px;background:linear-gradient(180deg,rgba(40,95,88,0.06),rgba(255,250,244,0.9));border:1px solid var(--line);text-align:center;margin-bottom:48px;}
.calc-wrap h2{font-family:'DM Serif Display',serif;font-size:clamp(28px,3vw,36px);margin-bottom:8px;}
.calc-wrap > p{color:var(--soft);font-size:15px;margin-bottom:24px;}
.calc-row{display:flex;align-items:center;justify-content:center;gap:16px;flex-wrap:wrap;margin-bottom:20px;}
.calc-input{padding:14px 18px;border-radius:14px;border:1.5px solid var(--line);background:#fff;font-size:16px;font-weight:700;width:160px;text-align:center;font-family:inherit;color:var(--ink);}
.calc-input:focus{outline:none;border-color:var(--green);}
.calc-result{padding:20px;border-radius:18px;background:rgba(255,255,255,0.8);border:1px solid var(--line);display:inline-block;min-width:280px;}
.calc-result strong{display:block;font-family:'DM Serif Display',serif;font-size:40px;color:var(--green);letter-spacing:-0.03em;}
.calc-result span{color:var(--soft);font-size:13px;font-weight:600;}

/* CTA */
.final-cta{padding:40px;border-radius:28px;background:linear-gradient(135deg,rgba(40,95,88,0.10),rgba(192,57,27,0.06));border:1px solid var(--line);text-align:center;}
.final-cta h2{font-family:'DM Serif Display',serif;font-size:clamp(28px,3.5vw,42px);line-height:1.05;letter-spacing:-0.03em;margin-bottom:8px;}
.final-cta p{color:var(--soft);font-size:15px;line-height:1.7;max-width:480px;margin:0 auto 24px;}

/* Footer */
footer{padding:32px 0 48px;color:var(--muted);font-size:13px;}
.footer-inner{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;}
.footer-links{display:flex;gap:20px;}
.footer-links a{color:var(--soft);font-weight:700;transition:color .15s;}
.footer-links a:hover{color:var(--accent);}

@media(max-width:960px){.aff-social{grid-template-columns:repeat(2,1fr);}.how-grid{grid-template-columns:1fr;}.ben-grid{grid-template-columns:1fr;}}
@media(max-width:720px){.aff-social{grid-template-columns:1fr;}.aff-hero h1{font-size:36px;}}
</style>
</head>
<body>

<nav>
  <div class="wrap nav-inner">
    <a href="/" class="brand">Sin<span>yal</span></a>
    <div class="nav-cta">
      <a class="btn btn-ghost" href="/">Beranda</a>
      <a class="btn btn-green" href="/signup?ref=affiliate">Gabung Sekarang</a>
    </div>
  </div>
</nav>

<!-- Hero -->
<header class="aff-hero">
  <div class="wrap">
    <div class="aff-hero-badge">Affiliate Program</div>
    <h1>Share tools.<br>Dapat <em>20% komisi.</em></h1>
    <p>Jadi affiliate Sinyal dan dapatkan komisi dari setiap orang yang upgrade lewat link referral kamu. Gratis, tanpa syarat, tanpa batas.</p>
    <div class="aff-hero-actions">
      <a class="btn btn-green btn-lg" href="/signup?ref=affiliate">Gabung Affiliate &rarr;</a>
      <a class="btn btn-ghost btn-lg" href="#cara-kerja">Cara Kerjanya</a>
    </div>
  </div>
</header>

<!-- Social Proof Stats -->
<div class="wrap">
  <div class="aff-social" id="affSocialStats">
    <div class="aff-social-item"><div class="aff-social-num" id="asPctNum">20%</div><div class="aff-social-label">Komisi</div></div>
    <div class="aff-social-item"><div class="aff-social-num" id="asAffNum">-</div><div class="aff-social-label">Affiliate Aktif</div></div>
    <div class="aff-social-item"><div class="aff-social-num" id="asRefNum">-</div><div class="aff-social-label">Total Referral</div></div>
    <div class="aff-social-item"><div class="aff-social-num">Rp50rb</div><div class="aff-social-label">Min. Payout</div></div>
  </div>
</div>

<!-- How It Works -->
<section id="cara-kerja">
  <div class="wrap">
    <div class="how-header">
      <h2>Cara Kerjanya</h2>
      <p>Tiga langkah simpel. Tidak perlu skill marketing.</p>
    </div>
    <div class="how-grid">
      <div class="how-card">
        <div class="how-num">1</div>
        <h3>Buat Akun &amp; Aktifkan</h3>
        <p>Daftar akun Sinyal (gratis), masuk ke dashboard, buka tab <strong>Affiliate</strong>, dan klik "Aktifkan". Link referral unik langsung dibuat.</p>
      </div>
      <div class="how-card">
        <div class="how-num">2</div>
        <h3>Bagikan Link Kamu</h3>
        <p>Share link referral kamu ke teman, followers, di bio, konten, grup Telegram &mdash; terserah kamu. Siapa saja yang klik dan daftar tercatat sebagai referral.</p>
      </div>
      <div class="how-card">
        <div class="how-num">3</div>
        <h3>Terima Komisi 20%</h3>
        <p>Setiap kali referral kamu upgrade ke paket berbayar (Weekly, Kredit, Starter, atau Pro), kamu otomatis dapat <strong>20% dari pembayaran</strong> mereka. Langsung masuk saldo.</p>
      </div>
    </div>
  </div>
</section>

<!-- Calculator -->
<section>
  <div class="wrap">
    <div class="calc-wrap">
      <h2>&#128176; Kalkulator Komisi</h2>
      <p>Hitung potensi penghasilan affiliate kamu.</p>
      <div class="calc-row">
        <div>
          <label style="display:block;font-size:12px;font-weight:800;color:var(--muted);text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;">Referral per bulan</label>
          <input class="calc-input" type="number" id="calcRefs" value="10" min="1" max="9999">
        </div>
        <div>
          <label style="display:block;font-size:12px;font-weight:800;color:var(--muted);text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;">Rata-rata paket</label>
          <select class="calc-input" id="calcPlan" style="width:180px;cursor:pointer;">
            <option value="29000">Weekly (Rp29rb)</option>
            <option value="49000">Starter (Rp49rb)</option>
            <option value="99000" selected>Pro (Rp99rb)</option>
          </select>
        </div>
      </div>
      <div class="calc-result">
        <strong id="calcAmount">Rp198.000</strong>
        <span>estimasi komisi / bulan</span>
      </div>
    </div>
  </div>
</section>

<!-- Benefits -->
<section>
  <div class="wrap">
    <div class="how-header">
      <h2>Kenapa Jadi Affiliate Sinyal?</h2>
      <p>Bukan cuma komisi besar &mdash; ini program yang beneran bikin untung.</p>
    </div>
    <div class="ben-grid">
      <div class="ben-card">
        <div class="ben-icon">&#128176;</div>
        <div><h3>20% Komisi Recurring</h3><p>Selama referral kamu tetap berlangganan, kamu tetap dapat komisi setiap bulan. Bukan sekali saja.</p></div>
      </div>
      <div class="ben-card">
        <div class="ben-icon">&#9889;</div>
        <div><h3>Dashboard Real-time</h3><p>Pantau jumlah klik, signup, konversi, dan saldo kamu dari dashboard affiliate yang lengkap.</p></div>
      </div>
      <div class="ben-card">
        <div class="ben-icon">&#128179;</div>
        <div><h3>Withdraw Mudah</h3><p>Tarik saldo via transfer bank (BCA, BNI, Mandiri, dll) atau e-wallet (GoPay, OVO, DANA). Minimum Rp50.000.</p></div>
      </div>
      <div class="ben-card">
        <div class="ben-icon">&#127775;</div>
        <div><h3>100% Gratis</h3><p>Tidak ada biaya pendaftaran. Bahkan akun Free pun bisa langsung ikut program affiliate.</p></div>
      </div>
      <div class="ben-card">
        <div class="ben-icon">&#128202;</div>
        <div><h3>Produk Bagus = Mudah Dijual</h3><p>Sinyal solve masalah nyata creator dan UMKM. Konversi tinggi karena orang memang butuh tools ini.</p></div>
      </div>
      <div class="ben-card">
        <div class="ben-icon">&#128230;</div>
        <div><h3>Tanpa Batas Referral</h3><p>Tidak ada cap. Mau 10 referral atau 10.000, komisi tetap 20% untuk semuanya.</p></div>
      </div>
    </div>
  </div>
</section>

<!-- FAQ -->
<section>
  <div class="wrap">
    <div class="faq-header"><h2>FAQ</h2></div>
    <div class="faq-list">
      <details class="faq-item">
        <summary>Apakah harus bayar untuk ikut program affiliate?</summary>
        <p>Tidak. Program affiliate Sinyal 100% gratis. Kamu cukup buat akun dan aktifkan di tab Affiliate di dashboard.</p>
      </details>
      <details class="faq-item">
        <summary>Berapa persen komisi yang didapat?</summary>
        <p>20% dari setiap transaksi yang dilakukan referral kamu. Misalnya paket Pro Rp99.000, kamu dapat Rp19.800.</p>
      </details>
      <details class="faq-item">
        <summary>Apakah komisinya recurring?</summary>
        <p>Ya! Selama referral kamu tetap berlangganan dan membayar, kamu akan terus mendapat komisi setiap bulannya.</p>
      </details>
      <details class="faq-item">
        <summary>Kapan dan bagaimana cara withdraw?</summary>
        <p>Kamu bisa request payout kapan saja selama saldo &ge; Rp50.000. Isi detail bank/e-wallet di dashboard, lalu klik "Request Payout". Proses 1-3 hari kerja.</p>
      </details>
      <details class="faq-item">
        <summary>Berapa lama cookie referral bertahan?</summary>
        <p>Referral tercatat saat seseorang sign up lewat link kamu. Selama mereka daftar lewat link tersebut, komisi kamu aman.</p>
      </details>
      <details class="faq-item">
        <summary>Apakah ada minimum referral?</summary>
        <p>Tidak ada. Bahkan 1 referral pun sudah bisa menghasilkan komisi. Tidak ada target atau deadline.</p>
      </details>
    </div>
  </div>
</section>

<!-- Final CTA -->
<section>
  <div class="wrap final-cta">
    <h2>Mulai sekarang, gratis.</h2>
    <p>Buat akun Sinyal, aktifkan affiliate link, dan mulai share. Komisi 20% langsung masuk saldo kamu.</p>
    <div style="display:flex;gap:12px;justify-content:center;flex-wrap:wrap;">
      <a class="btn btn-green btn-lg" href="/signup?ref=affiliate">Daftar &amp; Aktifkan Affiliate &rarr;</a>
      <a class="btn btn-ghost btn-lg" href="/#harga">Lihat Paket Sinyal</a>
    </div>
  </div>
</section>

<footer>
  <div class="wrap footer-inner">
    <span>&copy; 2026 Sinyal. Content intelligence untuk creator Indonesia.</span>
    <div class="footer-links">
      <a href="/">Beranda</a>
      <a href="/#harga">Harga</a>
      <a href="/signup">Daftar</a>
      <a href="/signin">Masuk</a>
    </div>
  </div>
</footer>

<script>
/* Load public stats */
(async function(){
  try {
    var res = await fetch('/api/affiliate/public-stats');
    var d = await res.json();
    var el1 = document.getElementById('asAffNum');
    var el2 = document.getElementById('asRefNum');
    if (el1) el1.textContent = (d.total_affiliates || 0).toLocaleString('id-ID');
    if (el2) el2.textContent = (d.total_referrals || 0).toLocaleString('id-ID');
  } catch(e) { console.warn('stats load failed', e); }
})();

/* Commission calculator */
function updateCalc() {
  var refs = parseInt(document.getElementById('calcRefs').value) || 0;
  var plan = parseInt(document.getElementById('calcPlan').value) || 0;
  var commission = Math.round(refs * plan * 0.2);
  document.getElementById('calcAmount').textContent = 'Rp' + commission.toLocaleString('id-ID');
}
document.getElementById('calcRefs').addEventListener('input', updateCalc);
document.getElementById('calcPlan').addEventListener('change', updateCalc);
updateCalc();
</script>
</body>
</html>"""


ACCOUNT_HTML = """<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="utf-8"/>
<meta content="width=device-width, initial-scale=1.0" name="viewport"/>
<title>Sinyal | Akun Saya</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=DM+Serif+Display:ital@0;1&display=swap" rel="stylesheet">
<style>
:root {
  --bg: #faf3ec;
  --ink: #3b1a08;
  --soft: #705b4c;
  --muted: #9a8474;
  --line: rgba(84,52,29,0.08);
  --card: rgba(255,250,244,0.9);
  --accent: #c0391b;
  --accent-2: #ef5a29;
  --orange: #ef5a29;
  --orange-2: #ff8d42;
  --green: #285f58;
}
*{box-sizing:border-box;margin:0;padding:0;}
html{scroll-behavior:smooth;}
body{font-family:'Plus Jakarta Sans',sans-serif;color:var(--ink);background:var(--bg);-webkit-font-smoothing:antialiased;}
a{text-decoration:none;color:inherit;}
.wrap{width:min(900px,100% - 40px);margin:0 auto;}

/* Nav */
nav{position:sticky;top:0;z-index:50;background:rgba(250,243,236,0.85);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);border-bottom:1px solid var(--line);}
.nav-inner{display:flex;align-items:center;justify-content:space-between;height:64px;}
.brand{font-family:'DM Serif Display',serif;font-size:24px;letter-spacing:-0.03em;color:var(--accent);font-weight:400;}
.nav-actions{display:flex;align-items:center;gap:8px;}
.nav-link{font-size:14px;font-weight:600;color:var(--soft);padding:8px 14px;border-radius:10px;transition:all .15s;}
.nav-link:hover{background:rgba(192,57,27,0.06);color:var(--accent);}

/* Buttons */
.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;padding:12px 22px;border-radius:12px;font-weight:800;font-size:14px;border:none;cursor:pointer;transition:transform .15s,box-shadow .15s;}
.btn:hover{transform:translateY(-1px);}
.btn-primary{background:linear-gradient(135deg,var(--accent),var(--accent-2));color:#fff;box-shadow:0 8px 24px rgba(192,57,27,0.22);}
.btn-primary:hover{box-shadow:0 12px 32px rgba(192,57,27,0.32);}
.btn-ghost{background:rgba(255,255,255,0.7);border:1.5px solid var(--line);color:var(--ink);font-weight:700;font-size:14px;padding:10px 18px;border-radius:10px;}
.btn-ghost:hover{border-color:var(--accent);color:var(--accent);}

/* Cards */
.card{background:var(--card);border:1px solid var(--line);border-radius:20px;box-shadow:0 12px 40px rgba(98,66,43,0.06);}

/* Profile header */
.profile-header{padding:32px;display:flex;align-items:center;gap:24px;flex-wrap:wrap;}
.avatar{width:72px;height:72px;border-radius:50%;background:linear-gradient(135deg,var(--orange),var(--orange-2));display:flex;align-items:center;justify-content:center;color:#fff;font-size:28px;font-weight:800;font-family:'DM Serif Display',serif;flex-shrink:0;}
.profile-info{flex:1;min-width:0;}
.profile-info h1{font-family:'DM Serif Display',serif;font-size:28px;letter-spacing:-0.03em;line-height:1.1;}
.profile-info .email{font-size:14px;color:var(--soft);margin-top:4px;}
.plan-badge{display:inline-flex;padding:6px 14px;border-radius:999px;background:rgba(192,57,27,0.08);color:var(--accent);font-size:12px;font-weight:800;letter-spacing:0.06em;text-transform:uppercase;}

/* Stats grid */
.stats-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-top:20px;}
.stat-card{padding:24px;text-align:center;}

/* Quota ring */
.ring-wrap{position:relative;width:100px;height:100px;margin:0 auto 12px;}
.ring-svg{width:100%;height:100%;transform:rotate(-90deg);}
.ring-track{fill:none;stroke:rgba(84,52,29,0.06);stroke-width:8;}
.ring-fill{fill:none;stroke:var(--accent);stroke-width:8;stroke-linecap:round;stroke-dasharray:283;stroke-dashoffset:283;transition:stroke-dashoffset 1s ease-out;}
.ring-label{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;}
.ring-num{font-family:'DM Serif Display',serif;font-size:28px;color:var(--accent);letter-spacing:-0.03em;line-height:1;}
.ring-sub{font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:0.08em;color:var(--muted);margin-top:2px;}

.stat-title{font-size:12px;font-weight:800;text-transform:uppercase;letter-spacing:0.06em;color:var(--muted);margin-bottom:10px;}
.stat-value{font-family:'DM Serif Display',serif;font-size:32px;color:var(--accent);letter-spacing:-0.03em;line-height:1;}
.stat-desc{font-size:13px;color:var(--soft);margin-top:6px;line-height:1.5;}

/* Feature list */
.feature-grid{padding:24px;display:grid;gap:8px;}
.feature-item{display:flex;align-items:center;gap:10px;padding:10px 14px;border-radius:12px;background:rgba(255,255,255,0.6);border:1px solid rgba(84,52,29,0.04);font-size:14px;color:var(--soft);font-weight:600;}
.feature-check{color:var(--accent);font-weight:800;font-size:16px;flex-shrink:0;}

/* Platforms */
.platform-row{display:flex;flex-wrap:wrap;gap:8px;padding:24px;}
.platform-pill{padding:8px 16px;border-radius:999px;background:rgba(255,255,255,0.7);border:1px solid var(--line);font-size:13px;font-weight:700;}

/* Quick links */
.quick-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-top:20px;}
.quick-link{padding:20px;display:flex;align-items:center;gap:14px;transition:transform .15s,box-shadow .15s;}
.quick-link:hover{transform:translateY(-2px);box-shadow:0 12px 32px rgba(98,66,43,0.08);border-color:rgba(192,57,27,0.2);}
.quick-icon{width:40px;height:40px;border-radius:12px;background:rgba(192,57,27,0.08);display:flex;align-items:center;justify-content:center;flex-shrink:0;}
.quick-icon svg{width:18px;height:18px;}
.quick-text h4{font-size:14px;font-weight:800;margin-bottom:2px;}
.quick-text p{font-size:12px;color:var(--muted);}

/* Section labels */
.section-label{display:flex;align-items:center;gap:8px;margin-bottom:14px;padding:0 4px;}
.section-label span{font-size:12px;font-weight:800;text-transform:uppercase;letter-spacing:0.06em;color:var(--muted);}

/* Responsive */
@media(max-width:720px){
  .stats-grid,.quick-grid{grid-template-columns:1fr;}
  .profile-header{flex-direction:column;align-items:flex-start;}
  .nav-actions{gap:4px;}
}
</style>
</head>
<body>

<nav>
  <div class="wrap nav-inner">
    <a href="/" class="brand">Sinyal</a>
    <div class="nav-actions">
      <a href="/app" class="nav-link">App</a>
      <a href="/payment" class="nav-link">Paket</a>
      <button onclick="doSignout()" class="btn-ghost" style="font-size:13px;">Keluar</button>
    </div>
  </div>
</nav>

<main style="padding:40px 0 80px;">
  <div class="wrap">

    <!-- Profile Header -->
    <div class="card profile-header">
      <div class="avatar" id="avatarCircle">?</div>
      <div class="profile-info">
        <h1>Akun Saya</h1>
        <p class="email" id="userEmail">Memuat...</p>
      </div>
      <div class="plan-badge" id="planBadge">&mdash;</div>
    </div>

    <!-- Stats -->
    <div class="stats-grid">
      <div class="card stat-card">
        <div class="ring-wrap">
          <svg class="ring-svg" viewBox="0 0 100 100">
            <circle class="ring-track" cx="50" cy="50" r="45"/>
            <circle class="ring-fill" id="quotaRing" cx="50" cy="50" r="45"/>
          </svg>
          <div class="ring-label">
            <span class="ring-num" id="quotaNum">&ndash;</span>
            <span class="ring-sub">tersisa</span>
          </div>
        </div>
        <div class="stat-title">Pencarian Hari Ini</div>
        <p class="stat-desc" id="quotaDetail">&ndash;</p>
      </div>

      <div class="card stat-card">
        <div class="stat-title">Paket Aktif</div>
        <div class="stat-value" id="planName">&ndash;</div>
        <p id="planPrice" style="font-size:16px;font-weight:800;color:var(--accent);margin-top:6px;">&ndash;</p>
        <p id="planTagline" class="stat-desc">&ndash;</p>
        <a href="/payment" class="btn btn-primary" style="width:100%;margin-top:16px;font-size:13px;" id="upgradeLink">Kelola Paket</a>
      </div>

      <div class="card" style="padding:24px;">
        <div class="stat-title">Fitur Aktif</div>
        <div id="featureList" class="feature-grid" style="padding:0;margin-top:10px;"></div>
      </div>
    </div>

    <!-- Platforms -->
    <div style="margin-top:20px;">
      <div class="section-label">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10 15.3 15.3 0 014-10z"/></svg>
        <span>Platform Aktif</span>
      </div>
      <div class="card">
        <div class="platform-row" id="platformList"></div>
      </div>
    </div>

    <!-- Quick Links -->
    <div class="quick-grid">
      <a href="/app" class="card quick-link">
        <div class="quick-icon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="color:var(--accent);"><path d="M4 17l6-6-6-6M12 19h8"/></svg>
        </div>
        <div class="quick-text"><h4>Buka App</h4><p>Riset &amp; analisis</p></div>
      </a>
      <a href="/payment" class="card quick-link">
        <div class="quick-icon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="color:var(--accent);"><rect x="1" y="4" width="22" height="16" rx="2"/><path d="M1 10h22"/></svg>
        </div>
        <div class="quick-text"><h4>Lihat Paket</h4><p>Upgrade &amp; billing</p></div>
      </a>
      <a href="/" class="card quick-link">
        <div class="quick-icon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="color:var(--accent);"><path d="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>
        </div>
        <div class="quick-text"><h4>Beranda</h4><p>Kembali ke landing</p></div>
      </a>
    </div>

  </div>
</main>

<script>
async function doSignout() {
  try { await fetch('/api/auth/signout', { method: 'POST' }); } catch(e) {}
  document.cookie = 'sinyal_access_token=; Max-Age=0; path=/';
  document.cookie = 'sinyal_refresh_token=; Max-Age=0; path=/';
  window.location.href = '/signin';
}

function fmtPrice(n) {
  if (!n) return 'Rp0';
  if (n >= 1000) return 'Rp' + Math.round(n/1000) + 'rb';
  return 'Rp' + n;
}

async function loadAccount() {
  try {
    const res = await fetch('/api/account/usage');
    if (!res.ok) { window.location.href = '/signin'; return; }
    const data = await res.json();
    if (!data.configured) return;
    if (data.error) { window.location.href = '/signin'; return; }

    const user = data.user || {};
    const profile = data.profile || {};
    const plan = data.plan || {};
    const tier = plan.code || 'free';

    document.getElementById('userEmail').textContent = user.email || '\u2014';
    document.getElementById('avatarCircle').textContent = (user.email || '?')[0].toUpperCase();
    document.getElementById('planBadge').textContent = tier.toUpperCase();
    document.getElementById('planName').textContent = plan.name || 'Free';
    document.getElementById('planPrice').textContent = fmtPrice(plan.price_idr) + (plan.billing_interval === 'monthly' ? '/bulan' : '');
    document.getElementById('planTagline').textContent = plan.tagline || '';

    // Quota ring (SVG circle)
    const dailyLimit = plan.daily_search_limit || 0;
    const left = profile.daily_searches_left ?? 0;
    const ring = document.getElementById('quotaRing');
    const circumference = 2 * Math.PI * 45; // r=45
    if (dailyLimit > 0) {
      const used = dailyLimit - left;
      const pct = Math.min(1, used / dailyLimit);
      ring.style.strokeDashoffset = circumference * (1 - pct);
      document.getElementById('quotaNum').textContent = left;
      document.getElementById('quotaDetail').textContent = used + ' dari ' + dailyLimit + ' dipakai hari ini';
    } else {
      ring.style.strokeDashoffset = circumference * 0.95;
      document.getElementById('quotaNum').textContent = '\u221e';
      document.getElementById('quotaDetail').textContent = 'Unlimited pencarian';
    }

    // Features
    const features = plan.limits || [];
    document.getElementById('featureList').innerHTML = features.map(f =>
      '<div class="feature-item"><span class="feature-check">\u2713</span>' + f + '</div>'
    ).join('');

    // Platforms
    const platforms = plan.allowed_platforms || ['tiktok'];
    const platformIcons = { tiktok: '🎵 TikTok', instagram: '📸 Instagram', youtube: '▶️ YouTube', twitter: '🐦 X', facebook: '📘 Facebook' };
    document.getElementById('platformList').innerHTML = platforms.map(p =>
      '<span class="platform-pill">' + (platformIcons[p] || p) + '</span>'
    ).join('');

    // Upgrade button state
    if (tier === 'pro') {
      document.getElementById('upgradeLink').textContent = 'Paket Aktif \u2713';
      document.getElementById('upgradeLink').style.opacity = '0.6';
      document.getElementById('upgradeLink').style.pointerEvents = 'none';
    }
  } catch(e) { console.warn('account load failed', e); }
}
loadAccount();
</script>
</body>
</html>"""


APP_HTML = """<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="utf-8"/>
<meta content="width=device-width, initial-scale=1.0" name="viewport"/>
<title>Sinyal | Workspace</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=DM+Serif+Display:ital@0;1&display=swap" rel="stylesheet">
<style>
:root {
  --bg: #faf3ec;
  --bg-soft: #fff9f2;
  --bg-white: rgba(255,255,255,0.7);
  --ink: #20160f;
  --soft: #705b4c;
  --muted: #9a8474;
  --line: rgba(84,52,29,0.08);
  --card: rgba(255,250,244,0.92);
  --accent: #c0391b;
  --accent-2: #ef5a29;
  --orange: #ef5a29;
  --orange-2: #ff8d42;
  --green: #285f58;
  --green-soft: rgba(40,95,88,0.08);
  --radius: 18px;
  --shadow-sm: 0 2px 8px rgba(98,66,43,0.04);
  --shadow: 0 8px 32px rgba(98,66,43,0.06);
  --shadow-lg: 0 20px 48px rgba(98,66,43,0.10);
}
*{box-sizing:border-box;margin:0;padding:0;}
html{scroll-behavior:smooth;}
body{font-family:'Plus Jakarta Sans',sans-serif;color:var(--ink);background:var(--bg);-webkit-font-smoothing:antialiased;overflow:hidden;height:100vh;}
a{text-decoration:none;color:inherit;}
::selection{background:rgba(192,57,27,0.12);}

/* ── Layout ── */
.app-layout{display:flex;height:100vh;overflow:hidden;}
.sidebar{width:260px;flex-shrink:0;background:linear-gradient(180deg,var(--bg-soft) 0%,#f5ece1 100%);border-right:1px solid var(--line);display:flex;flex-direction:column;padding:20px 14px;overflow-y:auto;z-index:40;transition:transform .25s cubic-bezier(.4,0,.2,1);}
.main{flex:1;display:flex;flex-direction:column;min-width:0;overflow:hidden;}

/* ── Sidebar ── */
.sidebar-brand{display:flex;align-items:center;gap:10px;padding:6px 10px;margin-bottom:28px;}
.sidebar-brand-icon{width:38px;height:38px;border-radius:12px;background:linear-gradient(135deg,var(--orange),var(--orange-2));display:flex;align-items:center;justify-content:center;box-shadow:0 6px 16px rgba(239,90,41,0.25);}
.sidebar-brand-icon svg{width:18px;height:18px;fill:#fff;}
.sidebar-brand h1{font-family:'DM Serif Display',serif;font-size:22px;color:var(--accent);letter-spacing:-0.03em;line-height:1;}
.sidebar-brand p{font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:0.08em;color:var(--muted);}

.sidebar-section-label{font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:0.1em;color:var(--muted);padding:0 12px;margin:20px 0 8px;}

.sidebar-nav{display:flex;flex-direction:column;gap:2px;flex:1;}
.nav-item{display:flex;align-items:center;gap:10px;padding:11px 14px;border-radius:14px;font-size:14px;font-weight:600;color:var(--soft);cursor:pointer;transition:all .15s ease;border:none;background:none;text-align:left;width:100%;text-decoration:none;position:relative;}
.nav-item:hover{background:rgba(239,90,41,0.06);color:var(--accent);}
.nav-item.active{background:rgba(239,90,41,0.10);color:var(--accent);font-weight:800;}
.nav-item.active::before{content:"";position:absolute;left:0;top:50%;transform:translateY(-50%);width:3px;height:20px;border-radius:0 3px 3px 0;background:linear-gradient(180deg,var(--accent),var(--accent-2));}
.nav-item svg{width:18px;height:18px;flex-shrink:0;opacity:0.65;}
.nav-item.active svg{opacity:1;}
.nav-divider{height:1px;background:var(--line);margin:6px 0;}

.sidebar-footer{margin-top:auto;padding:16px;border-radius:16px;background:linear-gradient(135deg,rgba(239,90,41,0.08),rgba(255,141,66,0.06));border:1px solid rgba(239,90,41,0.12);}
.sidebar-footer p{font-size:12px;color:var(--soft);margin-bottom:12px;line-height:1.5;}
.sidebar-footer a{display:flex;align-items:center;justify-content:center;width:100%;padding:11px;border-radius:12px;background:linear-gradient(135deg,var(--accent),var(--accent-2));color:#fff;font-weight:800;font-size:13px;transition:transform .15s,box-shadow .15s;box-shadow:0 6px 20px rgba(239,90,41,0.2);}
.sidebar-footer a:hover{transform:translateY(-1px);box-shadow:0 10px 28px rgba(239,90,41,0.3);}

/* ── Header ── */
.app-header{display:flex;align-items:center;justify-content:space-between;padding:0 28px;height:64px;border-bottom:1px solid var(--line);background:rgba(255,250,244,0.75);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);flex-shrink:0;}
.header-search{position:relative;flex:1;max-width:400px;}
.header-search svg{position:absolute;left:14px;top:50%;transform:translateY(-50%);color:var(--muted);pointer-events:none;}
.header-search input{width:100%;padding:10px 14px 10px 40px;border-radius:12px;border:1.5px solid var(--line);background:var(--bg-white);font:inherit;font-size:13px;color:var(--ink);outline:none;transition:border-color .2s,box-shadow .2s;}
.header-search input:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(239,90,41,0.08);}
.header-actions{display:flex;align-items:center;gap:8px;}
.header-avatar{width:36px;height:36px;border-radius:50%;background:linear-gradient(135deg,var(--accent),var(--accent-2));color:#fff;display:flex;align-items:center;justify-content:center;font-weight:800;font-size:14px;font-family:'DM Serif Display',serif;box-shadow:0 4px 12px rgba(239,90,41,0.2);transition:transform .15s;}
.header-avatar:hover{transform:scale(1.08);}

/* ── Content ── */
.content{flex:1;overflow-y:auto;padding:28px;background:var(--bg);}
.tab-section{display:none;}
.tab-section.active{display:block;animation:fadeUp .3s ease;}
@keyframes fadeUp{from{opacity:0;transform:translateY(8px);}to{opacity:1;transform:none;}}

/* ── Page Title ── */
.page-title{font-family:'DM Serif Display',serif;font-size:28px;letter-spacing:-0.03em;line-height:1.1;}
.page-subtitle{font-size:14px;color:var(--soft);margin-top:6px;line-height:1.6;}

/* ── Panel / Card ── */
.panel{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow);padding:28px;transition:box-shadow .2s;}
.panel:hover{box-shadow:var(--shadow-lg);}
.panel h3{font-family:'DM Serif Display',serif;font-size:22px;letter-spacing:-0.02em;margin-bottom:6px;}
.panel .desc{font-size:14px;color:var(--soft);line-height:1.65;margin-bottom:20px;}
.section-label{display:flex;align-items:center;gap:8px;font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:0.08em;color:var(--muted);margin-bottom:16px;}
.section-label svg{width:14px;height:14px;opacity:0.6;}

/* ── Inputs ── */
.ds-input{width:100%;padding:12px 16px;border-radius:14px;border:1.5px solid var(--line);background:var(--bg-white);font:inherit;font-size:14px;color:var(--ink);outline:none;transition:border-color .2s,box-shadow .2s;}
.ds-input:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(239,90,41,0.08);}
.ds-input::placeholder{color:var(--muted);}
select.ds-input{appearance:none;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%239a8474' stroke-width='2'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 14px center;padding-right:2.5rem;}
textarea.ds-input{resize:vertical;min-height:80px;}

/* ── Platform chips ── */
.chip-group{display:flex;flex-wrap:wrap;gap:8px;}
.chip-label input{display:none;}
.chip-label span{display:inline-flex;align-items:center;gap:6px;padding:9px 16px;border-radius:999px;background:var(--bg-white);border:1.5px solid var(--line);font-size:13px;font-weight:700;color:var(--soft);cursor:pointer;transition:all .2s;}
.chip-label input:checked + span{background:linear-gradient(135deg,var(--accent),var(--accent-2));border-color:transparent;color:#fff;box-shadow:0 4px 12px rgba(239,90,41,0.2);}
.chip-label span:hover{border-color:rgba(239,90,41,0.3);color:var(--accent);}

/* ── Filter row ── */
.filter-row{display:grid;gap:10px;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));margin-top:14px;}

/* ── Buttons ── */
.btn-action{display:inline-flex;align-items:center;gap:8px;padding:13px 24px;border-radius:14px;font-weight:800;font-size:14px;border:none;cursor:pointer;background:linear-gradient(135deg,var(--accent),var(--accent-2));color:#fff;box-shadow:0 8px 24px rgba(239,90,41,0.2);transition:transform .15s,box-shadow .15s;}
.btn-action:hover{transform:translateY(-1px);box-shadow:0 12px 32px rgba(239,90,41,0.3);}
.btn-action:active{transform:translateY(0);}
.btn-dl{display:inline-flex;align-items:center;gap:6px;padding:10px 16px;border-radius:12px;background:var(--bg-white);border:1.5px solid var(--line);font-weight:700;font-size:13px;color:var(--ink);transition:all .15s;}
.btn-dl:hover{border-color:var(--accent);color:var(--accent);}

/* ── Dashboard ── */
.dash-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:24px;}
.dash-action{padding:24px;display:flex;align-items:flex-start;gap:16px;cursor:pointer;transition:all .2s ease;border:1.5px solid transparent !important;}
.dash-action:hover{transform:translateY(-3px);box-shadow:var(--shadow-lg);border-color:rgba(239,90,41,0.15) !important;}
.dash-icon{width:48px;height:48px;border-radius:14px;display:flex;align-items:center;justify-content:center;flex-shrink:0;}
.dash-icon svg{width:22px;height:22px;}
.dash-action h4{font-size:15px;font-weight:800;margin-bottom:4px;}
.dash-action p{font-size:12px;color:var(--muted);line-height:1.5;}

/* ── Platforms ── */
.platform-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;}
.platform-item{display:flex;align-items:center;gap:10px;padding:12px 16px;border-radius:14px;background:var(--bg-white);border:1.5px solid var(--line);font-size:13px;font-weight:700;color:var(--ink);transition:all .15s;}
.platform-item:hover{border-color:rgba(239,90,41,0.2);transform:translateY(-1px);}
.platform-item svg{width:18px;height:18px;flex-shrink:0;}

/* ── Steps ── */
.steps-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;}
.step-item{text-align:center;padding:20px 16px;border-radius:16px;background:var(--bg-white);border:1.5px solid var(--line);transition:all .15s;}
.step-item:hover{border-color:rgba(239,90,41,0.15);transform:translateY(-1px);}
.step-num{width:36px;height:36px;border-radius:50%;background:linear-gradient(135deg,rgba(239,90,41,0.12),rgba(255,141,66,0.08));color:var(--accent);font-weight:800;font-size:15px;display:flex;align-items:center;justify-content:center;margin:0 auto 10px;font-family:'DM Serif Display',serif;}
.step-item strong{display:block;font-size:14px;margin-bottom:4px;}
.step-item span{font-size:12px;color:var(--muted);line-height:1.4;}

/* ── Quota ── */
.quota-banner{padding:18px 22px;border-radius:16px;background:linear-gradient(135deg,rgba(255,250,244,0.95),rgba(255,255,255,0.9));border:1.5px solid var(--line);box-shadow:var(--shadow-sm);margin-bottom:24px;display:none;}
.quota-inner{display:flex;align-items:center;gap:18px;flex-wrap:wrap;}
.quota-info{flex:1;min-width:200px;}
.quota-label{display:flex;align-items:center;gap:8px;font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:0.06em;color:var(--muted);margin-bottom:8px;}
.quota-bar-wrap{height:6px;border-radius:3px;background:rgba(84,52,29,0.06);overflow:hidden;}
.quota-bar{height:100%;border-radius:3px;background:linear-gradient(90deg,var(--orange),var(--orange-2));transition:width .6s cubic-bezier(.4,0,.2,1);}
.quota-text{font-size:12px;font-weight:700;color:var(--soft);margin-top:6px;}
.quota-upgrade{padding:10px 18px;border-radius:12px;background:linear-gradient(135deg,var(--accent),var(--accent-2));color:#fff;font-weight:800;font-size:12px;white-space:nowrap;box-shadow:0 4px 12px rgba(239,90,41,0.15);transition:transform .15s;}
.quota-upgrade:hover{transform:translateY(-1px);}

/* ── Dashboard intel ── */
.intel-board{display:grid;gap:16px;}
.intel-panel{background:linear-gradient(135deg,rgba(255,255,255,0.96),rgba(255,250,244,0.92));border:1.5px solid var(--line);border-radius:18px;padding:20px;box-shadow:var(--shadow-sm);}
.intel-toolbar{display:grid;grid-template-columns:180px 1fr 1fr auto;gap:10px;align-items:end;}
.intel-toolbar .ds-input{min-width:0;}
.intel-toolbar .btn-dl{justify-content:center;height:46px;}
.intel-filter-meta{margin-top:10px;font-size:12px;font-weight:700;color:var(--muted);line-height:1.5;}
.intel-header{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap;margin-bottom:14px;}
.intel-header h3{margin:0;font-size:20px;}
.intel-sub{font-size:13px;color:var(--muted);line-height:1.6;margin-top:4px;}
.intel-badges{display:flex;gap:8px;flex-wrap:wrap;}
.intel-badge{display:inline-flex;align-items:center;gap:6px;padding:7px 12px;border-radius:999px;background:rgba(239,90,41,0.08);border:1px solid rgba(239,90,41,0.14);font-size:11px;font-weight:800;color:var(--accent);text-transform:uppercase;letter-spacing:0.05em;}
.intel-summary-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;}
.intel-summary-card{padding:16px;border-radius:16px;background:var(--bg-white);border:1.5px solid var(--line);}
.intel-summary-card span{display:block;font-size:11px;font-weight:800;color:var(--muted);text-transform:uppercase;letter-spacing:0.06em;margin-bottom:8px;}
.intel-summary-card strong{display:block;font-size:22px;line-height:1.1;color:var(--ink);font-family:'DM Serif Display',serif;}
.intel-summary-card small{display:block;margin-top:8px;font-size:12px;color:var(--soft);line-height:1.5;}
.intel-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:16px;}
.intel-list-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;}
.intel-card{padding:16px;border-radius:16px;background:var(--bg-white);border:1.5px solid var(--line);display:grid;gap:12px;}
.intel-rank{display:inline-flex;align-items:center;justify-content:center;min-width:32px;height:32px;padding:0 10px;border-radius:999px;background:linear-gradient(135deg,var(--accent),var(--accent-2));color:#fff;font-size:12px;font-weight:800;box-shadow:0 6px 14px rgba(239,90,41,0.18);}
.intel-title{font-size:15px;font-weight:800;color:var(--ink);line-height:1.5;margin:0;}
.intel-handle{font-size:13px;color:var(--accent);font-weight:800;}
.intel-signature{font-size:12px;color:var(--soft);line-height:1.5;}
.intel-metrics{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;}
.intel-metric{padding:10px 12px;border-radius:12px;background:rgba(255,250,244,0.72);border:1px solid rgba(84,52,29,0.06);}
.intel-metric span{display:block;font-size:10px;font-weight:800;color:var(--muted);text-transform:uppercase;letter-spacing:0.05em;margin-bottom:4px;}
.intel-metric strong{font-size:14px;color:var(--ink);line-height:1.3;}
.intel-chips{display:flex;flex-wrap:wrap;gap:6px;}
.intel-chip{display:inline-flex;align-items:center;padding:5px 10px;border-radius:999px;background:rgba(40,95,88,0.08);color:var(--green);font-size:11px;font-weight:700;}
.intel-raw{border-top:1px dashed rgba(84,52,29,0.12);padding-top:10px;}
.intel-raw summary{cursor:pointer;font-size:12px;font-weight:800;color:var(--accent);}
.intel-raw pre{margin:10px 0 0;padding:12px;border-radius:12px;background:#fffaf4;border:1px solid rgba(84,52,29,0.08);font-size:11px;line-height:1.6;color:var(--soft);white-space:pre-wrap;word-break:break-word;max-height:220px;overflow:auto;}
.intel-mini-table{display:grid;gap:10px;}
.intel-row{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;padding:12px 14px;border-radius:14px;background:var(--bg-white);border:1.5px solid var(--line);}
.intel-row strong{display:block;font-size:13px;color:var(--ink);line-height:1.45;}
.intel-row span{display:block;font-size:11px;color:var(--muted);margin-top:4px;line-height:1.4;}
.intel-empty{padding:18px;border-radius:16px;background:rgba(255,255,255,0.74);border:1.5px dashed var(--line);font-size:13px;color:var(--muted);line-height:1.6;}

/* ── Result cards ── */
.result-card{background:var(--card);border:1.5px solid var(--line);border-radius:var(--radius);padding:0;margin-bottom:12px;transition:all .2s ease;overflow:hidden;display:flex;flex-direction:row;}
.result-card .result-thumb{width:160px;min-width:160px;height:auto;min-height:180px;object-fit:cover;background:linear-gradient(135deg,rgba(239,90,41,0.08),rgba(255,141,66,0.04));flex-shrink:0;display:block;}
.result-card .result-thumb-placeholder{width:160px;min-width:160px;min-height:180px;background:linear-gradient(135deg,rgba(239,90,41,0.08),rgba(255,141,66,0.04));display:flex;align-items:center;justify-content:center;flex-shrink:0;}
.result-card .result-thumb-placeholder svg{width:40px;height:40px;opacity:0.25;color:var(--accent);}
.result-card .result-body{padding:22px;flex:1;min-width:0;}
@media(max-width:640px){.result-card{flex-direction:column;}.result-card .result-thumb,.result-card .result-thumb-placeholder{width:100%;min-width:100%;height:180px;min-height:180px;}}
.result-card:hover{transform:translateY(-2px);box-shadow:var(--shadow-lg);border-color:rgba(239,90,41,0.12);}
.result-card:hover .result-thumb{opacity:0.92;}
.result-meta{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:10px;}
.result-badge{padding:5px 12px;border-radius:999px;background:linear-gradient(135deg,rgba(239,90,41,0.1),rgba(255,141,66,0.06));color:var(--accent);font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:0.04em;}
.result-author{font-size:12px;color:var(--muted);font-weight:600;}
.result-title{font-size:15px;font-weight:800;line-height:1.45;margin-bottom:8px;display:block;color:var(--ink);transition:color .15s;}
.result-title:hover{color:var(--accent);}
.result-caption{font-size:13px;color:var(--soft);line-height:1.65;margin-bottom:12px;}
.result-transcript{margin-bottom:12px;padding:14px 16px;border-radius:14px;background:rgba(255,255,255,0.6);border-left:3px solid rgba(239,90,41,0.25);font-size:12px;color:var(--soft);font-style:italic;line-height:1.7;}
.insight-bar{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px;}
.insight-chip{display:inline-flex;align-items:center;gap:4px;padding:5px 10px;border-radius:20px;font-size:11px;font-weight:700;line-height:1;white-space:nowrap;}
.insight-chip.hook-type{background:rgba(40,95,88,0.1);color:#285f58;}
.insight-chip.hook-score{background:rgba(239,90,41,0.1);color:var(--accent);}
.insight-chip.cta-type{background:rgba(59,130,246,0.1);color:#2563eb;}
.insight-chip.angle{background:rgba(168,85,247,0.1);color:#7c3aed;}
.insight-idea{margin-bottom:10px;padding:10px 14px;border-radius:12px;background:linear-gradient(135deg,rgba(40,95,88,0.06),rgba(40,95,88,0.02));border:1px dashed rgba(40,95,88,0.15);font-size:12px;color:var(--green);line-height:1.6;}
.insight-idea strong{font-size:10px;text-transform:uppercase;letter-spacing:0.06em;display:block;margin-bottom:3px;color:var(--green);opacity:0.7;}
.affiliate-badge{display:inline-flex;align-items:center;gap:5px;padding:4px 10px;border-radius:20px;font-size:11px;font-weight:800;background:rgba(255,183,0,0.12);color:#b8860b;margin-bottom:6px;}
.btn-products{display:inline-flex;align-items:center;gap:5px;padding:6px 14px;border-radius:10px;font-size:12px;font-weight:700;background:linear-gradient(135deg,#fff7e6,#fff3d6);color:#b8860b;border:1px solid rgba(255,183,0,0.3);cursor:pointer;transition:all .15s;margin-bottom:10px;}
.btn-products:hover{background:linear-gradient(135deg,#fff3d6,#ffedbd);border-color:#b8860b;}
.btn-products:disabled{opacity:0.5;cursor:wait;}
.product-cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:10px;margin-bottom:12px;}
.product-card{background:linear-gradient(135deg,#fffdf8,#fff9f0);border:1px solid rgba(255,183,0,0.2);border-radius:14px;padding:14px;font-size:12px;}
.product-card .p-name{font-weight:800;font-size:13px;color:var(--ink);margin-bottom:6px;line-height:1.4;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;}
.product-card .p-price{font-size:16px;font-weight:800;color:#c0391b;}
.product-card .p-original{font-size:12px;color:var(--muted);text-decoration:line-through;margin-left:6px;}
.product-card .p-discount{display:inline-block;padding:2px 6px;border-radius:6px;font-size:10px;font-weight:800;background:rgba(192,57,27,0.1);color:#c0391b;margin-left:6px;}
.product-card .p-meta{display:flex;flex-wrap:wrap;gap:8px;margin-top:6px;font-size:11px;color:var(--soft);}
.product-card .p-meta span{display:inline-flex;align-items:center;gap:3px;}
.product-card .p-shop{margin-top:6px;font-size:11px;color:var(--muted);font-weight:600;}
.result-stats{display:flex;gap:16px;flex-wrap:wrap;align-items:center;}
.result-stat{display:flex;align-items:center;gap:5px;font-size:12px;color:var(--muted);}
.result-stat strong{color:var(--ink);font-weight:800;}
.result-stat svg{width:14px;height:14px;opacity:0.6;}
.result-tags{display:flex;gap:4px;flex-wrap:wrap;margin-left:auto;}
.result-tag{padding:3px 10px;border-radius:8px;background:rgba(84,52,29,0.04);font-size:10px;color:var(--muted);font-weight:700;}
.copy-btn{display:inline-flex;align-items:center;gap:5px;padding:7px 12px;border-radius:10px;background:rgba(239,90,41,0.08);color:var(--accent);font-size:11px;font-weight:800;border:none;cursor:pointer;transition:all .15s;flex-shrink:0;}
.copy-btn:hover{background:rgba(239,90,41,0.16);transform:scale(1.03);}
.save-btn{display:inline-flex;align-items:center;gap:5px;padding:7px 12px;border-radius:10px;background:rgba(40,95,88,0.10);color:var(--green);font-size:11px;font-weight:800;border:none;cursor:pointer;transition:all .15s;flex-shrink:0;}
.save-btn:hover{background:rgba(40,95,88,0.18);transform:scale(1.03);}
.save-btn.saved{background:rgba(40,95,88,0.2);color:var(--green);}

/* ── Saved tab ── */
.saved-toolbar{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:14px;}
.saved-item{padding:16px;border-radius:14px;background:var(--bg-white);border:1px solid var(--line);display:grid;gap:8px;}
.saved-item-top{display:flex;justify-content:space-between;align-items:flex-start;gap:10px;}
.saved-item-meta{display:flex;gap:8px;flex-wrap:wrap;font-size:11px;color:var(--muted);font-weight:700;}

/* ── Profile analytics ── */
.analytics-panel{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow);padding:24px;font-size:14px;color:var(--soft);}

/* ── Comment cards ── */
.comment-card{background:var(--bg-white);border:1.5px solid var(--line);border-radius:16px;padding:16px;border-left:3px solid var(--accent);transition:all .15s;}
.comment-card:hover{border-color:rgba(239,90,41,0.2);box-shadow:var(--shadow-sm);}
.comment-card strong{display:block;font-size:13px;color:var(--accent);margin-bottom:6px;font-weight:800;}
.comment-card p{font-size:14px;color:var(--ink);line-height:1.6;}

/* ── Empty states ── */
.empty-state{display:flex;flex-direction:column;align-items:center;justify-content:center;padding:40px 20px;text-align:center;color:var(--muted);}
.empty-state svg{margin-bottom:16px;opacity:0.3;}
.empty-state p{font-size:14px;line-height:1.6;}

/* ── Hamburger ── */
.hamburger{display:none;background:none;border:none;cursor:pointer;padding:8px;flex-direction:column;gap:5px;}
.hamburger span{display:block;width:20px;height:2px;background:var(--ink);border-radius:2px;transition:all .2s;}
.sidebar-overlay{position:fixed;inset:0;background:rgba(0,0,0,0.35);z-index:35;display:none;backdrop-filter:blur(4px);}

/* ── Responsive ── */
@media(max-width:768px){
  .sidebar{position:fixed;left:0;top:0;height:100%;transform:translateX(-100%);z-index:40;box-shadow:4px 0 24px rgba(0,0,0,0.1);}
  .sidebar.open{transform:translateX(0);}
  .sidebar-overlay.open{display:block;}
  .hamburger{display:flex !important;}
  .dash-grid,.steps-grid{grid-template-columns:1fr;}
  .intel-toolbar{grid-template-columns:1fr;}
  .intel-summary-grid,.intel-grid,.intel-list-grid{grid-template-columns:1fr;}
  .platform-grid{grid-template-columns:repeat(2,1fr);}
  .content{padding:16px;}
  .app-header{padding:0 16px;}
  .filter-row{grid-template-columns:1fr 1fr;}
  .page-title{font-size:22px;}
}
</style>
</head>
<body>

<div class="app-layout">
  <div class="sidebar-overlay" id="sidebarOverlay" onclick="closeSidebar()"></div>

  <!-- ── SIDEBAR ── -->
  <aside class="sidebar" id="sidebar">
    <a href="/" class="sidebar-brand" style="text-decoration:none;">
      <div class="sidebar-brand-icon">
        <svg viewBox="0 0 24 24"><path d="M3.5 18.49l6-6.01 4 4L22 6.92l-1.41-1.41-7.09 7.97-4-4L2 16.99z"/></svg>
      </div>
      <div>
        <h1>Sinyal</h1>
        <p>Content Intel</p>
      </div>
    </a>

    <div class="sidebar-section-label">Workspace</div>
    <nav class="sidebar-nav">
      <button type="button" class="nav-item active" data-tab="dashboard" onclick="switchTab('dashboard')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>
        <span>Beranda</span>
      </button>
      <button type="button" class="nav-item" data-tab="search" onclick="switchTab('search')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>
        <span>Riset</span>
      </button>
      <button type="button" class="nav-item" data-tab="profile" onclick="switchTab('profile')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
        <span>Profil</span>
      </button>
      <button type="button" class="nav-item" data-tab="comments" onclick="switchTab('comments')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg>
        <span>Komentar</span>
      </button>
      <button type="button" class="nav-item" data-tab="saved" onclick="switchTab('saved')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 21l-7-5-7 5V5a2 2 0 012-2h10a2 2 0 012 2z"/></svg>
        <span>Saved</span>
      </button>
      <button type="button" class="nav-item" data-tab="affiliate" onclick="switchTab('affiliate')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/><circle cx="8.5" cy="7" r="4"/><path d="M20 8v6M23 11h-6"/></svg>
        <span>Affiliate</span>
      </button>

      <div class="nav-divider"></div>
      <div class="sidebar-section-label">Pengaturan</div>

      <a href="/account" class="nav-item">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
        <span>Akun</span>
      </a>
      <a href="/payment" class="nav-item">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="1" y="4" width="22" height="16" rx="2"/><path d="M1 10h22"/></svg>
        <span>Billing</span>
      </a>
      <button onclick="doSignout()" class="nav-item" style="border:none;">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4M16 17l5-5-5-5M21 12H9"/></svg>
        <span>Keluar</span>
      </button>
    </nav>

    <div class="sidebar-footer" id="sidebarUpgradeCard">
      <p style="font-weight:700;">Unlock semua platform &amp; fitur tanpa batas.</p>
      <a href="/payment" id="sidebarUpgradeBtn">Upgrade ke Pro &rarr;</a>
    </div>
  </aside>

  <!-- ── MAIN ── -->
  <div class="main">
    <header class="app-header">
      <div style="display:flex;align-items:center;gap:12px;flex:1;max-width:440px;">
        <button class="hamburger" onclick="toggleSidebar()">
          <span></span><span></span><span></span>
        </button>
        <div class="header-search">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>
          <input id="globalSearch" placeholder="Cari cepat..." type="text"/>
        </div>
      </div>
      <div class="header-actions">
        <a href="/account" class="header-avatar" title="Akun">A</a>
      </div>
    </header>

    <div class="content" id="contentArea">

      <!-- ═══ DASHBOARD ═══ -->
      <section id="dashboardTab" class="tab-section active">
        <div style="margin-bottom:28px;">
          <h2 class="page-title">Selamat datang di Sinyal</h2>
          <p class="page-subtitle">Riset konten, analisis creator, dan baca pasar dari satu tempat.</p>
        </div>

        <!-- Quota -->
        <div class="quota-banner" id="quotaBanner">
          <div class="quota-inner">
            <div class="quota-info">
              <div class="quota-label">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>
                <span id="quotaPlanName">Free</span>
                <span id="quotaTierBadge" style="padding:3px 10px;border-radius:999px;background:linear-gradient(135deg,rgba(239,90,41,0.1),rgba(255,141,66,0.06));color:var(--accent);font-size:10px;font-weight:800;letter-spacing:0.04em;"></span>
              </div>
              <div class="quota-bar-wrap">
                <div class="quota-bar" id="quotaBar" style="width:0%"></div>
              </div>
              <div class="quota-text" id="quotaText">&ndash;</div>
            </div>
            <a href="/payment" class="quota-upgrade" id="quotaUpgradeBtn" style="display:none;">Upgrade</a>
          </div>
        </div>

        <!-- Quick actions -->
        <div class="dash-grid">
          <a href="#search" class="panel dash-action">
            <div class="dash-icon" style="background:linear-gradient(135deg,rgba(239,90,41,0.12),rgba(255,141,66,0.06));">
              <svg viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>
            </div>
            <div>
              <h4>Riset Keyword</h4>
              <p>Scan 5 platform sekaligus, filter views &amp; tanggal</p>
            </div>
          </a>
          <a href="#profile" class="panel dash-action">
            <div class="dash-icon" style="background:linear-gradient(135deg,rgba(40,95,88,0.12),rgba(40,95,88,0.06));">
              <svg viewBox="0 0 24 24" fill="none" stroke="var(--green)" stroke-width="2"><path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
            </div>
            <div>
              <h4>Analisis Profil</h4>
              <p>Performa &amp; pola konten creator TikTok</p>
            </div>
          </a>
          <a href="#comments" class="panel dash-action">
            <div class="dash-icon" style="background:linear-gradient(135deg,rgba(121,85,72,0.1),rgba(121,85,72,0.04));">
              <svg viewBox="0 0 24 24" fill="none" stroke="#795548" stroke-width="2"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg>
            </div>
            <div>
              <h4>Baca Komentar</h4>
              <p>Sentiment, feedback, dan bahasa pasar audiens</p>
            </div>
          </a>
        </div>        <!-- Platforms -->
        <div class="panel" style="margin-bottom:20px;">
          <div class="section-label">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10 15.3 15.3 0 014-10z"/></svg>
            Platform yang didukung
          </div>
          <div class="platform-grid">
            <div class="platform-item"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M19.59 6.69a4.83 4.83 0 01-3.77-4.25V2h-3.45v13.67a2.89 2.89 0 01-2.88 2.5 2.89 2.89 0 01-2.89-2.89 2.89 2.89 0 012.89-2.89c.28 0 .54.04.79.1V9.01a6.27 6.27 0 00-.79-.05 6.34 6.34 0 00-6.34 6.34 6.34 6.34 0 006.34 6.34 6.34 6.34 0 006.33-6.34V8.75a8.18 8.18 0 004.77 1.52V6.84a4.84 4.84 0 01-1-.15z"/></svg>TikTok</div>
            <div class="platform-item"><svg viewBox="0 0 24 24" fill="#FF0000"><path d="M23.5 6.2a3.02 3.02 0 00-2.12-2.14C19.5 3.5 12 3.5 12 3.5s-7.5 0-9.38.56A3.02 3.02 0 00.5 6.2 31.7 31.7 0 000 12a31.7 31.7 0 00.5 5.8 3.02 3.02 0 002.12 2.14c1.87.56 9.38.56 9.38.56s7.5 0 9.38-.56a3.02 3.02 0 002.12-2.14A31.7 31.7 0 0024 12a31.7 31.7 0 00-.5-5.8zM9.54 15.52V8.48L15.82 12l-6.28 3.52z"/></svg>YouTube</div>
            <div class="platform-item"><svg viewBox="0 0 24 24" fill="#E1306C"><path d="M12 2.16c3.2 0 3.58.01 4.85.07 3.25.15 4.77 1.69 4.92 4.92.06 1.27.07 1.65.07 4.85 0 3.2-.01 3.58-.07 4.85-.15 3.23-1.66 4.77-4.92 4.92-1.27.06-1.64.07-4.85.07-3.2 0-3.58-.01-4.85-.07-3.26-.15-4.77-1.7-4.92-4.92-.06-1.27-.07-1.65-.07-4.85 0-3.2.01-3.58.07-4.85C2.38 3.86 3.9 2.31 7.15 2.23 8.42 2.17 8.8 2.16 12 2.16zM12 0C8.74 0 8.33.01 7.05.07 2.7.27.27 2.7.07 7.05.01 8.33 0 8.74 0 12s.01 3.67.07 4.95c.2 4.36 2.62 6.78 6.98 6.98C8.33 23.99 8.74 24 12 24s3.67-.01 4.95-.07c4.35-.2 6.78-2.62 6.98-6.98.06-1.28.07-1.69.07-4.95s-.01-3.67-.07-4.95c-.2-4.35-2.63-6.78-6.98-6.98C15.67.01 15.26 0 12 0zm0 5.84A6.16 6.16 0 1018.16 12 6.16 6.16 0 0012 5.84zM12 16a4 4 0 110-8 4 4 0 010 8zm6.4-11.85a1.44 1.44 0 100 2.88 1.44 1.44 0 000-2.88z"/></svg>Instagram</div>
            <div class="platform-item"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/></svg>X (Twitter)</div>
            <div class="platform-item"><svg viewBox="0 0 24 24" fill="#1877F2"><path d="M24 12.07C24 5.41 18.63 0 12 0S0 5.4 0 12.07C0 18.1 4.39 23.1 10.13 24v-8.44H7.08v-3.49h3.04V9.41c0-3.02 1.79-4.7 4.53-4.7 1.31 0 2.68.24 2.68.24v2.97h-1.51c-1.49 0-1.95.93-1.95 1.89v2.26h3.32l-.53 3.5h-2.8V24C19.62 23.1 24 18.1 24 12.07z"/></svg>Facebook</div>
          </div>
        </div>

        <!-- How it works -->
        <div class="panel">
          <div class="section-label">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
            Cara Kerja
          </div>
          <div class="steps-grid">
            <div class="step-item"><div class="step-num">1</div><strong>Ketik keyword</strong><span>Pilih platform &amp; filter</span></div>
            <div class="step-item"><div class="step-num">2</div><strong>Sinyal scan</strong><span>Views, likes, caption, transkrip</span></div>
            <div class="step-item"><div class="step-num">3</div><strong>AI Analysis</strong><span>Hook, angle, audience insight</span></div>
          </div>
        </div>

        <div class="panel" style="margin-top:24px;">
          <div class="section-label">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>
            Filter Tanggal Market Pulse
          </div>
          <div class="intel-toolbar">
            <select id="intelPreset" class="ds-input">
              <option value="today">Hari ini</option>
              <option value="7d">7 hari terakhir</option>
              <option value="30d">30 hari terakhir</option>
              <option value="custom">Custom</option>
            </select>
            <input id="intelStartDate" type="date" class="ds-input"/>
            <input id="intelEndDate" type="date" class="ds-input"/>
            <button id="intelApplyBtn" type="button" class="btn-dl">Terapkan</button>
          </div>
          <div id="intelFilterMeta" class="intel-filter-meta">Menampilkan data untuk hari ini.</div>
        </div>

        <div id="dashboardActivity" style="margin-top:24px;"></div>
      </section>

      <!-- ═══ SEARCH ═══ -->
      <section id="searchTab" class="tab-section">
        <div style="margin-bottom:24px;">
          <h2 class="page-title">Riset Konten</h2>
          <p class="page-subtitle">Cari keyword, pilih platform, lalu analisis sinyal konten yang sedang naik.</p>
        </div>
        <div class="panel">
          <div>
            <label class="section-label" style="margin-bottom:8px;">Keyword</label>
            <textarea id="keywordInput" class="ds-input" placeholder="Masukkan keyword (satu per baris untuk multi-keyword)..." rows="2">openai</textarea>
          </div>

          <div style="margin-top:16px;">
            <label class="section-label" style="margin-bottom:8px;">Platform</label>
            <div class="chip-group" id="platformChips">
              <label class="chip-label"><input type="checkbox" value="tiktok" checked/><span>TikTok</span></label>
              <label class="chip-label"><input type="checkbox" value="youtube"/><span>YouTube</span></label>
              <label class="chip-label"><input type="checkbox" value="instagram"/><span>Instagram</span></label>
              <label class="chip-label"><input type="checkbox" value="twitter"/><span>X</span></label>
              <label class="chip-label"><input type="checkbox" value="facebook"/><span>Facebook</span></label>
            </div>
          </div>

          <div class="filter-row">
            <select id="sortBy" class="ds-input">
              <option value="relevance">Relevan</option>
              <option value="popular">Views &uarr;</option>
              <option value="most_liked">Likes &uarr;</option>
              <option value="latest">Terbaru</option>
            </select>
            <select id="dateRange" class="ds-input">
              <option value="all">Semua waktu</option>
              <option value="7d">7 hari</option>
              <option value="30d">30 hari</option>
            </select>
            <select id="perPlatform" class="ds-input">
              <option value="5">5 / platform</option>
              <option value="10" selected>10 / platform</option>
              <option value="20">20 / platform</option>
              <option value="30">30 / platform</option>
            </select>
            <input id="minViews" type="number" class="ds-input" placeholder="Min tayang"/>
          </div>
          <div class="filter-row" style="margin-top:8px;">
            <input id="maxViews" type="number" class="ds-input" placeholder="Max tayang"/>
            <input id="minLikes" type="number" class="ds-input" placeholder="Min suka"/>
            <input id="maxLikes" type="number" class="ds-input" placeholder="Max suka"/>
          </div>

          <div style="margin-top:22px;display:flex;flex-wrap:wrap;gap:10px;align-items:center;">
            <button id="searchBtn" class="btn-action">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>
              Scan Sinyal
            </button>
            <a id="jsonDownload" class="btn-dl" href="#" download style="display:none;">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg>
              JSON
            </a>
            <a id="csvDownload" class="btn-dl" href="#" download style="display:none;">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg>
              CSV
            </a>
            <a id="pdfDownload" class="btn-dl" href="#" download style="display:none;">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg>
              PDF
            </a>
          </div>
          <p id="searchMeta" style="margin-top:16px;font-size:13px;font-weight:700;color:var(--accent);"></p>
        </div>
        <div id="searchResults" style="margin-top:16px;"></div>
      </section>

      <!-- ═══ PROFILE ═══ -->
      <section id="profileTab" class="tab-section">
        <div style="margin-bottom:24px;">
          <h2 class="page-title">Profil Creator</h2>
          <p class="page-subtitle">Analisis pola konten, rata-rata performa, dan feed terbaru dari creator TikTok.</p>
        </div>
        <div style="display:grid;grid-template-columns:1fr 320px;gap:16px;">
          <div>
            <div class="panel" style="margin-bottom:16px;">
              <div style="display:flex;flex-wrap:wrap;gap:10px;">
                <input id="profileInput" class="ds-input" style="flex:1;min-width:180px;" placeholder="Masukkan username TikTok..." value="openai"/>
                <select id="profileSort" class="ds-input" style="width:auto;"><option value="latest">Terbaru</option><option value="popular">Popular</option></select>
                <select id="profileDateRange" class="ds-input" style="width:auto;"><option value="all">Semua</option><option value="7d">7 hari</option></select>
                <button id="profileLoadBtn" class="btn-action" style="padding:11px 20px;font-size:13px;">Muat Profil</button>
              </div>
              <input id="profileFeedSearch" class="ds-input" style="margin-top:12px;" placeholder="Filter di dalam feed profil ini..."/>
            </div>
            <div id="profileResults"></div>
          </div>
          <div class="analytics-panel" id="profileAnalytics">
            <div class="empty-state">
              <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="var(--line)" stroke-width="1.5"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
              <p>Masukkan username dan klik <strong>Muat Profil</strong> untuk melihat analitik creator.</p>
            </div>
          </div>
        </div>
      </section>

      <!-- ═══ COMMENTS ═══ -->
      <section id="commentsTab" class="tab-section">
        <div style="margin-bottom:24px;">
          <h2 class="page-title">Komentar Intel</h2>
          <p class="page-subtitle">Ambil dan analisis komentar dari video TikTok untuk menemukan insight audiens.</p>
        </div>
        <div style="display:grid;grid-template-columns:1fr 320px;gap:16px;">
          <div>
            <div class="panel" style="margin-bottom:16px;">
              <div style="display:grid;gap:10px;grid-template-columns:1fr 100px auto;">
                <input id="commentsUrl" class="ds-input" placeholder="URL video TikTok..." value="https://www.tiktok.com/@openai/video/7604654293966146829"/>
                <input id="commentsMax" type="number" class="ds-input" value="5" style="text-align:center;"/>
                <button id="commentsLoadBtn" class="btn-action" style="padding:11px 20px;font-size:13px;">Ekstrak</button>
              </div>
              <p id="commentsMeta" style="margin-top:14px;font-size:13px;font-weight:700;color:var(--accent);"></p>
            </div>
            <div id="commentsResults" style="display:grid;gap:10px;"></div>
          </div>
          <div class="analytics-panel">
            <div style="font-family:'DM Serif Display',serif;font-size:18px;color:var(--accent);margin-bottom:14px;">Comment Intelligence</div>
            <div style="padding:16px;border-radius:14px;background:var(--bg-white);border:1px solid var(--line);font-size:12px;color:var(--muted);line-height:2;">
              <p>&gt; Menunggu URL video...</p>
              <p>&gt; Klik Ekstrak untuk mulai.</p>
              <p style="animation:pulse 2s ease-in-out infinite;">_</p>
            </div>
          </div>
        </div>
      </section>

      <!-- ═══ SAVED ═══ -->
      <section id="savedTab" class="tab-section">
        <div style="margin-bottom:24px;">
          <h2 class="page-title">Saved List</h2>
          <p class="page-subtitle">Simpan script/video favorit ke playlist biar gampang dipakai ulang.</p>
        </div>
        <div class="panel">
          <div class="saved-toolbar">
            <select id="savedPlaylistSelect" class="ds-input" style="min-width:220px;max-width:320px;"></select>
            <button id="createPlaylistBtn" class="btn-dl" type="button">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14M5 12h14"/></svg>
              Playlist Baru
            </button>
            <button id="refreshSavedBtn" class="btn-dl" type="button">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 4v6h-6M1 20v-6h6"/><path d="M3.51 9a9 9 0 0114.13-3.36L23 10M1 14l5.36 4.36A9 9 0 0020.49 15"/></svg>
              Refresh
            </button>
          </div>
          <p id="savedMeta" style="margin-bottom:14px;font-size:13px;font-weight:700;color:var(--accent);"></p>
          <div id="savedResults" style="display:grid;gap:10px;"></div>
        </div>
      </section>

      <!-- ═══ AFFILIATE ═══ -->
      <section id="affiliateTab" class="tab-section">
        <div style="margin-bottom:24px;">
          <h2 class="page-title">Affiliate Program</h2>
          <p class="page-subtitle">Undang orang pakai link kamu. Setiap mereka bayar, kamu dapat komisi 20%.</p>
        </div>

        <!-- Not yet activated state -->
        <div id="affActivateCard" class="panel" style="text-align:center;padding:40px 24px;">
          <div style="font-size:48px;margin-bottom:16px;">&#128176;</div>
          <h3 style="font-size:22px;font-weight:800;margin-bottom:8px;">Mulai Dapat Komisi</h3>
          <p style="color:var(--soft);margin-bottom:20px;max-width:400px;margin-left:auto;margin-right:auto;line-height:1.65;">Aktifkan affiliate link kamu sekarang. Setiap orang yang daftar dan bayar lewat link kamu, kamu dapat 20% komisi.</p>
          <button id="affActivateBtn" class="btn btn-primary" type="button" onclick="activateAffiliate()" style="font-size:15px;padding:14px 28px;">Aktifkan Affiliate &rarr;</button>
        </div>

        <!-- Active affiliate dashboard (hidden until activated) -->
        <div id="affDashboard" style="display:none;">

          <!-- Referral Link -->
          <div class="panel" style="margin-bottom:16px;">
            <div class="section-label" style="margin-bottom:12px;">Link Referral Kamu</div>
            <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
              <input id="affRefLink" type="text" class="ds-input" readonly style="flex:1;min-width:240px;font-size:13px;font-weight:700;background:rgba(255,255,255,0.7);cursor:text;">
              <button onclick="copyAffLink()" class="btn btn-primary" type="button" style="font-size:13px;padding:10px 18px;white-space:nowrap;">Copy Link</button>
            </div>
            <p style="margin-top:8px;font-size:12px;color:var(--muted);">Bagikan link ini ke teman, follower, atau audiens kamu.</p>
          </div>

          <!-- Stats Cards -->
          <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:16px;">
            <div class="card stat-card">
              <div class="stat-title">Total Referral</div>
              <div class="stat-value" id="affTotalRefs">0</div>
              <p class="stat-desc">Orang daftar lewat link kamu</p>
            </div>
            <div class="card stat-card">
              <div class="stat-title">Converted</div>
              <div class="stat-value" id="affConvertedRefs">0</div>
              <p class="stat-desc">Yang sudah bayar paket</p>
            </div>
            <div class="card stat-card">
              <div class="stat-title">Total Komisi</div>
              <div class="stat-value" id="affLifetimeEarnings">Rp0</div>
              <p class="stat-desc">Total yang kamu hasilkan</p>
            </div>
            <div class="card stat-card">
              <div class="stat-title">Saldo Tersedia</div>
              <div class="stat-value" id="affPendingBalance" style="color:var(--green);">Rp0</div>
              <p class="stat-desc">Bisa ditarik kapan saja</p>
            </div>
          </div>

          <!-- Payout Section -->
          <div class="panel" style="margin-bottom:16px;">
            <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;margin-bottom:16px;">
              <div>
                <div class="section-label">Payout</div>
                <p style="font-size:12px;color:var(--muted);margin-top:4px;">Minimum payout: <strong id="affMinPayout">Rp50.000</strong></p>
              </div>
              <button onclick="requestPayout()" id="affPayoutBtn" class="btn btn-primary" type="button" style="font-size:13px;padding:10px 18px;">Tarik Saldo</button>
            </div>

            <!-- Payout Settings -->
            <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px;margin-bottom:14px;" id="affPayoutForm">
              <div>
                <label style="font-size:12px;font-weight:800;color:var(--muted);margin-bottom:4px;display:block;">Metode</label>
                <select id="affPayoutMethod" class="ds-input" style="width:100%;">
                  <option value="">Pilih metode...</option>
                  <option value="bank_transfer">Transfer Bank</option>
                  <option value="ewallet">E-Wallet (GoPay/OVO/Dana)</option>
                </select>
              </div>
              <div id="affBankFields" style="display:none;">
                <label style="font-size:12px;font-weight:800;color:var(--muted);margin-bottom:4px;display:block;">Nama Bank</label>
                <input id="affBankName" class="ds-input" placeholder="BCA, BNI, Mandiri..." style="width:100%;">
              </div>
              <div id="affAccountFields" style="display:none;">
                <label style="font-size:12px;font-weight:800;color:var(--muted);margin-bottom:4px;display:block;">No. Rekening / No. HP</label>
                <input id="affAccountNumber" class="ds-input" placeholder="1234567890" style="width:100%;">
              </div>
              <div id="affAccountNameField" style="display:none;">
                <label style="font-size:12px;font-weight:800;color:var(--muted);margin-bottom:4px;display:block;">Nama Pemilik</label>
                <input id="affAccountName" class="ds-input" placeholder="Nama sesuai rekening" style="width:100%;">
              </div>
            </div>
            <button onclick="savePayoutSettings()" class="btn-dl" type="button" style="font-size:12px;">Simpan Pengaturan Payout</button>
          </div>

          <!-- Referral List -->
          <div class="panel">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;">
              <div class="section-label">Daftar Referral</div>
              <button onclick="loadAffiliateTab()" class="btn-dl" type="button" style="font-size:12px;">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 4v6h-6M1 20v-6h6"/><path d="M3.51 9a9 9 0 0114.13-3.36L23 10M1 14l5.36 4.36A9 9 0 0020.49 15"/></svg>
                Refresh
              </button>
            </div>
            <div id="affReferralList" style="display:grid;gap:8px;">
              <p style="color:var(--muted);font-size:13px;">Belum ada referral.</p>
            </div>
          </div>

          <!-- Payout History -->
          <div class="panel" style="margin-top:16px;">
            <div class="section-label" style="margin-bottom:12px;">Riwayat Payout</div>
            <div id="affPayoutHistory" style="display:grid;gap:8px;">
              <p style="color:var(--muted);font-size:13px;">Belum ada payout.</p>
            </div>
          </div>

        </div>
      </section>

    </div>
  </div>
</div>

<script>
window.onerror = function(msg, src, line, col, err) {
  console.error('[SINYAL ERROR]', msg, 'at', src, line, col);
};

function getCookie(name) {
  var pattern = new RegExp('(?:^|; )' + name.replace(/[.$?*|{}()\\[\\]\\\\/\\+^]/g, '\\$&') + '=([^;]*)');
  var match = document.cookie.match(pattern);
  return match ? decodeURIComponent(match[1]) : '';
}

(function patchFetchWithAppGuard() {
  var originalFetch = window.fetch ? window.fetch.bind(window) : null;
  if (!originalFetch) return;
  window.fetch = function(resource, options) {
    var opts = options ? Object.assign({}, options) : {};
    var headers = new Headers(opts.headers || {});
    var guard = getCookie('sinyal_app_guard');
    if (guard) headers.set('x-sinyal-app-guard', guard);
    opts.headers = headers;
    if (!opts.credentials) opts.credentials = 'same-origin';
    return originalFetch(resource, opts);
  };
})();
/* ===== SIDEBAR ===== */
function toggleSidebar() {
  document.getElementById('sidebar').classList.toggle('open');
  document.getElementById('sidebarOverlay').classList.toggle('open');
}
function closeSidebar() {
  document.getElementById('sidebar').classList.remove('open');
  document.getElementById('sidebarOverlay').classList.remove('open');
}

/* ===== TABS ===== */
function switchTab(name) {
  var valid = ['dashboard','search','profile','comments','saved','affiliate'];
  if (!valid.includes(name)) name = 'dashboard';
  // Update nav items
  document.querySelectorAll('.nav-item[data-tab]').forEach(function(b) {
    b.classList.toggle('active', b.dataset.tab === name);
  });
  // Update tab sections
  document.querySelectorAll('.tab-section').forEach(function(s) {
    s.classList.remove('active');
  });
  var tab = document.getElementById(name + 'Tab');
  if (tab) tab.classList.add('active');
  if (name === 'saved') {
    loadSavedTab();
  }
  if (name === 'dashboard') {
    loadDashboardIntel();
  }
  if (name === 'affiliate') {
    loadAffiliateTab();
  }
}
// Handle direct URL navigation (e.g. /app#search)
(function() {
  var hash = (location.hash || '').replace('#', '');
  switchTab(hash || 'dashboard');
})();
window.addEventListener('hashchange', function() {
  var hash = (location.hash || '').replace('#', '');
  if (hash) switchTab(hash);
});

/* Quick action links */
document.querySelectorAll('.dash-action[href]').forEach(a => {
  a.addEventListener('click', e => {
    const hash = a.getAttribute('href');
    if (hash && hash.startsWith('#')) {
      e.preventDefault();
      location.hash = hash;
      switchTab(hash.replace('#', ''));
    }
  });
});

/* ===== XSS ===== */
function toggleCaption(btn) {
  var rid = btn.dataset.rid;
  var e = document.getElementById('cap_' + rid);
  if (!e) return;
  e.style.webkitLineClamp = e.style.webkitLineClamp === 'unset' ? '2' : 'unset';
  btn.textContent = btn.textContent === 'Selengkapnya' ? 'Tutup' : 'Selengkapnya';
}
function toggleTranscript(btn) {
  var rid = btn.dataset.rid;
  var a = document.getElementById('tr_' + rid);
  var b = document.getElementById('trf_' + rid);
  if (!a || !b) return;
  if (a.style.display === 'none') {
    a.style.display = '';
    b.style.display = 'none';
    btn.textContent = 'Tutup';
  } else {
    a.style.display = 'none';
    b.style.display = '';
    btn.textContent = 'Baca semua';
  }
}

/* ===== PRODUCT SCRAPING ===== */
async function loadProducts(btn, videoUrl) {
  if (!videoUrl) videoUrl = btn.getAttribute('data-url');
  btn.disabled = true;
  btn.textContent = '⏳ Mengambil data produk...';
  var container = btn.nextElementSibling;
  try {
    var resp = await fetch('/api/products?url=' + encodeURIComponent(videoUrl));
    var data = await resp.json();
    if (data.error) {
      btn.textContent = '❌ ' + data.error;
      btn.disabled = false;
      return;
    }
    if (!data.products || data.products.length === 0) {
      btn.textContent = '🔍 Tidak ada produk ditemukan';
      btn.style.opacity = '0.6';
      return;
    }
    btn.style.display = 'none';
    container.innerHTML = data.products.map(function(p) {
      var priceStr = p.price ? 'Rp' + Number(p.price).toLocaleString('id-ID') : '';
      var origStr = p.original_price && p.original_price > p.price ? 'Rp' + Number(p.original_price).toLocaleString('id-ID') : '';
      return '<div class="product-card">' +
        (p.thumbnail ? '<img src="' + escapeHTML(p.thumbnail) + '" style="width:100%;height:120px;object-fit:cover;border-radius:10px;margin-bottom:8px;" onerror="this.hidden=true" loading="lazy">' : '') +
        '<div class="p-name">' + escapeHTML(p.name || 'Produk #' + p.product_id) + '</div>' +
        '<div>' +
          (priceStr ? '<span class="p-price">' + priceStr + '</span>' : '') +
          (origStr ? '<span class="p-original">' + origStr + '</span>' : '') +
          (p.discount_pct ? '<span class="p-discount">-' + p.discount_pct + '%</span>' : '') +
        '</div>' +
        '<div class="p-meta">' +
          (p.sold_count ? '<span>📦 ' + escapeHTML(p.sold_count) + '</span>' : '') +
          (p.rating ? '<span>⭐ ' + p.rating.toFixed(1) + '</span>' : '') +
          (p.review_count ? '<span>💬 ' + p.review_count + ' review</span>' : '') +
          (p.commission_rate ? '<span>💰 Komisi ' + escapeHTML(p.commission_rate) + '</span>' : '') +
        '</div>' +
        (p.revenue ? '<div class="p-meta"><span>💵 Revenue: Rp' + Number(p.revenue).toLocaleString('id-ID') + '</span>' + (p.items_sold_count ? '<span>📊 ' + Number(p.items_sold_count).toLocaleString('id-ID') + ' terjual</span>' : '') + '</div>' : '') +
        (p.seller_type || p.ship_from ? '<div class="p-meta">' + (p.seller_type ? '<span>🏷️ ' + escapeHTML(p.seller_type) + '</span>' : '') + (p.ship_from ? '<span>🚚 ' + escapeHTML(p.ship_from) + '</span>' : '') + '</div>' : '') +
        (p.shop_name ? '<div class="p-shop">🏪 ' + escapeHTML(p.shop_name) + '</div>' : '') +
        '<a href="' + escapeHTML(p.product_url) + '" target="_blank" rel="noopener" style="display:inline-block;margin-top:8px;padding:5px 12px;border-radius:8px;font-size:11px;font-weight:700;background:rgba(255,183,0,0.15);color:#b8860b;text-decoration:none;border:1px solid rgba(255,183,0,0.3);">Lihat di TikTok Shop →</a>' +
      '</div>';
    }).join('');
  } catch(e) {
    btn.textContent = '❌ Gagal mengambil data produk';
    btn.disabled = false;
    console.error('Product fetch error:', e);
  }
}

function escapeHTML(str) {
  if (!str) return '';
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

/* ===== FORMAT ===== */
function formatNum(n) {
  if (n == null) return '-';
  n = Number(n);
  if (n >= 1000000) return (n/1000000).toFixed(1) + 'M';
  if (n >= 1000) return (n/1000).toFixed(1) + 'K';
  return n.toString();
}

function copyHook(text) {
  navigator.clipboard.writeText(text);
  event.target.textContent = 'Copied!';
  setTimeout(() => { event.target.textContent = 'Copy'; }, 1200);
}

/* ===== RESULT ROW ===== */
let _rowId = 0;
var RESULT_CACHE = {};
function rowResult(item) {
  const rid = _rowId++;
  RESULT_CACHE[rid] = item;
  const hook = escapeHTML(item.hook || item.title || item.caption || 'Tanpa judul');
  const rawHook = escapeHTML(item.hook || item.title || item.caption || 'Tanpa judul');
  const caption = escapeHTML(item.caption || item.content || item.description || '');
  const transcript = item.transcript ? escapeHTML(item.transcript) : '';
  const transcriptShort = transcript.length > 150 ? transcript.substring(0, 150) + '...' : transcript;
  const hashtags = (item.hashtags || []).slice(0, 5);
  const music = escapeHTML(item.music || '');
  const videoUrl = escapeHTML(item.video_url || '');
  const author = escapeHTML(item.author || '');
  const authorUrl = escapeHTML(item.author_url || '');
  const thumb = escapeHTML(item.thumbnail || '');

    return '<div class="result-card">' +
      (thumb ? '<a href="' + videoUrl + '" target="_blank" style="flex-shrink:0;display:block;"><img class="result-thumb" src="' + thumb + '" alt="" loading="lazy"></a>'
      : '<div class="result-thumb-placeholder"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><polygon points="5 3 19 12 5 21 5 3"/></svg></div>') +
    '<div class="result-body">' +
    '<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px;margin-bottom:8px;">' +
      '<div style="flex:1;min-width:0;">' +
        '<div class="result-meta">' +
          '<span class="result-badge">' + escapeHTML(item.platform || 'N/A') + '</span>' +
          (author ? (authorUrl ? '<a href="' + authorUrl + '" target="_blank" class="result-author" style="color:var(--muted);">@' + author + '</a>' : '<span class="result-author">@' + author + '</span>') : '') +
          (music ? '<span class="result-author" style="display:flex;align-items:center;gap:3px;">\u266B ' + music + '</span>' : '') +
        '</div>' +
        '<a href="' + videoUrl + '" target="_blank" class="result-title">' + hook + '</a>' +
      '</div>' +
      '<div style="display:flex;gap:6px;align-items:center;flex-shrink:0;">' +
        '<button onclick="copyHook(this.dataset.hook)" data-hook="' + rawHook + '" class="copy-btn">Copy</button>' +
        '<button onclick="saveResultById(' + rid + ', this)" class="save-btn">Save</button>' +
      '</div>' +
    '</div>' +
    // Insight bar
    '<div class="insight-bar">' +
      (item.hook_type ? '<span class="insight-chip hook-type">' + escapeHTML(item.hook_type) + '</span>' : '') +
      (item.hook_score ? '<span class="insight-chip hook-score">' + escapeHTML(item.hook_score) + '</span>' : '') +
      (item.cta_type && item.cta_type !== 'Tidak terdeteksi' ? '<span class="insight-chip cta-type">' + escapeHTML(item.cta_type) + '</span>' : '') +
      (item.angle ? '<span class="insight-chip angle">' + escapeHTML(item.angle) + '</span>' : '') +
    '</div>' +
    (item.content_idea ? '<div class="insight-idea"><strong>💡 Ide Konten</strong>' + escapeHTML(item.content_idea) + '</div>' : '') +
    // Affiliate product detection
    (item.has_affiliate ? '<div class="affiliate-badge">🛒 Produk Affiliate Terdeteksi</div>' : '') +
    (item.platform === 'tiktok' && item.has_affiliate ? '<button class="btn-products" onclick="loadProducts(this)" data-url="' + escapeHTML(item.video_url) + '">🛍️ Lihat Produk</button><div id="products_' + rid + '" class="product-cards"></div>' : '') +
    (item.platform === 'tiktok' && !item.has_affiliate && item.commerce_signals && item.commerce_signals.length ? '<div class="affiliate-badge" style="opacity:0.6;">🔍 Mungkin ada produk (' + item.commerce_signals.join(', ') + ')</div><button class="btn-products" onclick="loadProducts(this)" data-url="' + escapeHTML(item.video_url) + '">🛍️ Cek Produk</button><div id="products_' + rid + '" class="product-cards"></div>' : '') +
    (caption ? '<div class="result-caption"><p id="cap_' + rid + '" style="display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;">' + caption + '</p>' + (caption.length > 120 ? '<button onclick="toggleCaption(this)" data-rid="' + rid + '" style="font-size:12px;color:var(--accent);font-weight:800;background:none;border:none;cursor:pointer;margin-top:4px;">Selengkapnya</button>' : '') + '</div>' : '') +
    (transcript ? '<div class="result-transcript"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;"><span style="font-size:11px;font-weight:800;color:var(--accent);text-transform:uppercase;letter-spacing:0.04em;">Transcript</span>' + (transcript.length > 150 ? '<button onclick="toggleTranscript(this)" data-rid="' + rid + '" style="font-size:11px;color:var(--accent);font-weight:800;background:none;border:none;cursor:pointer;">Baca semua</button>' : '') + '</div><p id="trf_' + rid + '">' + transcriptShort + '</p><p id="tr_' + rid + '" style="display:none;">' + transcript + '</p></div>' : '') +
    '<div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;">' +
      '<div class="result-stats">' +
        '<span class="result-stat"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg><strong>' + formatNum(item.views) + '</strong></span>' +
        '<span class="result-stat"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20.84 4.61a5.5 5.5 0 00-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 00-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 000-7.78z"/></svg><strong>' + formatNum(item.likes) + '</strong></span>' +
        '<span class="result-stat"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg><strong>' + formatNum(item.comments) + '</strong></span>' +
        '<span class="result-stat"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="M8.59 13.51l6.83 3.98M15.41 6.51l-6.82 3.98"/></svg><strong>' + formatNum(item.shares) + '</strong></span>' +
      '</div>' +
      '<div class="result-tags">' + hashtags.map(function(h){return '<span class="result-tag">#' + escapeHTML(h) + '</span>';}).join('') + '</div>' +
    '</div>' +
    '</div>' +
  '</div>';
}

/* ===== SEARCH ===== */
async function runSearch() {
  var q = document.getElementById('keywordInput').value.trim();
  var platforms = Array.from(document.querySelectorAll('#platformChips input[type="checkbox"]:checked')).map(function(c){return c.value;}).join(',');
  if (!platforms) { document.getElementById('searchMeta').innerHTML = '<span style="color:var(--accent);">Pilih minimal 1 platform.</span>'; return; }
  var sort = document.getElementById('sortBy').value;
  var dateRange = document.getElementById('dateRange').value;
  var minViews = document.getElementById('minViews').value;
  var maxViews = document.getElementById('maxViews').value;
  var minLikes = document.getElementById('minLikes').value;
  var maxLikes = document.getElementById('maxLikes').value;
  var perPlatform = document.getElementById('perPlatform').value;
  var params = new URLSearchParams({ keyword: q, platforms: platforms, max_results: perPlatform, sort: sort, date_range: dateRange });
  if(minViews) params.set('min_views', minViews);
  if(maxViews) params.set('max_views', maxViews);
  if(minLikes) params.set('min_likes', minLikes);
  if(maxLikes) params.set('max_likes', maxLikes);

  var jsonLink = document.getElementById('jsonDownload');
  var csvLink = document.getElementById('csvDownload');
  var pdfLink = document.getElementById('pdfDownload');
  jsonLink.style.display = 'none';
  csvLink.style.display = 'none';
  pdfLink.style.display = 'none';
  document.getElementById('searchResults').innerHTML = '';

  // Progress bar UI
  var metaEl = document.getElementById('searchMeta');
  metaEl.innerHTML = '<div id="searchProgress" style="width:100%;">' +
    '<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">' +
      '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="animation:spin 1s linear infinite;flex-shrink:0;"><path d="M21 12a9 9 0 11-6.219-8.56"/></svg>' +
      '<span id="progressLabel" style="font-size:13px;font-weight:700;">Memulai scan...</span>' +
    '</div>' +
    '<div style="width:100%;height:6px;border-radius:99px;background:var(--line);overflow:hidden;">' +
      '<div id="progressBar" style="width:0%;height:100%;border-radius:99px;background:linear-gradient(90deg,var(--accent),var(--accent-2));transition:width .4s ease;"></div>' +
    '</div>' +
    '<div id="platformStatus" style="display:flex;flex-wrap:wrap;gap:6px;margin-top:8px;"></div>' +
  '</div>';

  try {
    var response = await fetch('/api/search/stream?' + params.toString());
    if (!response.ok) {
      var errData = await response.json().catch(function() { return {}; });
      if (response.status === 429 || response.status === 402 || response.status === 400) {
        metaEl.innerHTML = '<span style="color:var(--accent);font-weight:800;">TERTOLAK: ' + escapeHTML(errData.error || 'Error') + '</span> <a href="' + (errData.upgrade_url || '/payment') + '" style="margin-left:8px;display:inline-block;padding:8px 16px;border-radius:10px;background:linear-gradient(135deg,var(--accent),var(--accent-2));color:#fff;font-size:12px;font-weight:800;">Upgrade Sekarang</a>';
        return;
      }
      throw new Error(errData.error || 'Server error ' + response.status);
    }

    var reader = response.body.getReader();
    var decoder = new TextDecoder();
    var buffer = '';
    var streamResults = [];
    var platformsDone = {};

    while (true) {
      var chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
      var lines = buffer.split('\\n');
      buffer = lines.pop();

      for (var line of lines) {
        if (!line.startsWith('data: ')) continue;
        var evt;
        try { evt = JSON.parse(line.slice(6)); } catch(e) { continue; }

        if (evt.type === 'init') {
          var statusEl = document.getElementById('platformStatus');
          statusEl.innerHTML = evt.platforms.map(function(p) {
            return '<span id="pstat_' + p + '" style="display:inline-flex;align-items:center;gap:4px;padding:5px 10px;border-radius:999px;background:rgba(150,150,150,0.08);color:var(--muted);font-size:11px;font-weight:800;transition:all .3s ease;">' +
              '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" style="animation:spin 1s linear infinite;"><path d="M21 12a9 9 0 11-6.219-8.56"/></svg> ' +
              escapeHTML(getPlatformLabel(p)) + '</span>';
          }).join('');
        }

        if (evt.type === 'platform_done') {
          platformsDone[evt.platform] = evt.count;
          var chip = document.getElementById('pstat_' + evt.platform);
          if (chip) {
            chip.style.background = 'rgba(40,95,88,0.08)';
            chip.style.color = 'var(--green)';
            chip.innerHTML = '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg> ' + escapeHTML(getPlatformLabel(evt.platform)) + ': ' + evt.count;
          }
          // Append results progressively
          var resultsEl = document.getElementById('searchResults');
          for (var r of (evt.results || [])) {
            resultsEl.innerHTML += rowResult(r);
            streamResults.push(r);
          }
          // Update progress
          var bar = document.getElementById('progressBar');
          var label = document.getElementById('progressLabel');
          if (bar) bar.style.width = evt.progress + '%';
          if (label) label.textContent = getPlatformLabel(evt.platform) + ' selesai (' + evt.progress + '%) — ' + streamResults.length + ' hasil';
        }

        if (evt.type === 'platform_error') {
          var chip2 = document.getElementById('pstat_' + evt.platform);
          if (chip2) {
            chip2.style.background = 'rgba(239,90,41,0.08)';
            chip2.style.color = 'var(--accent)';
            chip2.innerHTML = '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> ' + escapeHTML(getPlatformLabel(evt.platform)) + ': error';
          }
          var bar2 = document.getElementById('progressBar');
          if (bar2) bar2.style.width = evt.progress + '%';
        }

        if (evt.type === 'done') {
          // Re-render with final sorted/filtered results
          _rowId = 0; RESULT_CACHE = {};
          document.getElementById('searchResults').innerHTML = (evt.results || []).map(rowResult).join('') || '<div class="empty-state" style="padding:32px;"><p>Tidak ada hasil untuk keyword ini.</p></div>';
          metaEl.innerHTML = '<span style="display:inline-flex;align-items:center;gap:6px;color:var(--green);"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg> ' + (evt.results ? evt.results.length : 0) + ' hasil ditemukan dalam ' + (evt.elapsed || '?') + '</span>' + renderPlatformBreakdown(evt.platform_breakdown);
          if(evt.json_file){ jsonLink.href = '/api/download?file=' + encodeURIComponent(evt.json_file); jsonLink.style.display = 'inline-flex'; }
          if(evt.csv_file){ csvLink.href = '/api/download?file=' + encodeURIComponent(evt.csv_file); csvLink.style.display = 'inline-flex'; }
          if(evt.pdf_file){ pdfLink.href = '/api/download?file=' + encodeURIComponent(evt.pdf_file); pdfLink.style.display = 'inline-flex'; }
        }
      }
    }
  } catch (err) {
    document.getElementById('searchMeta').innerHTML = '<span style="color:var(--accent);">ERROR: ' + escapeHTML(err.message) + '</span>';
  }
}

function getPlatformLabel(platform) {
  return {
    tiktok: 'TikTok',
    youtube: 'YouTube',
    instagram: 'Instagram',
    twitter: 'X',
    facebook: 'Facebook'
  }[platform] || platform;
}

function renderPlatformBreakdown(breakdown) {
  if (!breakdown) return '';
  var entries = Object.entries(breakdown);
  if (!entries.length) return '';
  return '<div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:8px;">' + entries.map(function(entry) {
    var platform = entry[0];
    var info = entry[1] || {};
    var count = Number(info.results || 0);
    var errorCount = Array.isArray(info.errors) ? info.errors.length : 0;
    var bg = count > 0 ? 'rgba(40,95,88,0.08)' : 'rgba(239,90,41,0.08)';
    var color = count > 0 ? 'var(--green)' : 'var(--accent)';
    var suffix = errorCount ? ' · ' + errorCount + ' error' : '';
    return '<span style="display:inline-flex;align-items:center;padding:6px 10px;border-radius:999px;background:' + bg + ';color:' + color + ';font-size:11px;font-weight:800;">' + escapeHTML(getPlatformLabel(platform) + ': ' + count + suffix) + '</span>';
  }).join('') + '</div>';
}

/* ===== PROFILE ===== */
async function loadProfile() {
  var username = document.getElementById('profileInput').value.trim();
  var sort = document.getElementById('profileSort').value;
  var dateRange = document.getElementById('profileDateRange').value;
  document.getElementById('profileAnalytics').innerHTML = '<div class="empty-state"><p style="display:inline-flex;align-items:center;gap:6px;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="animation:spin 1s linear infinite;"><path d="M21 12a9 9 0 11-6.219-8.56"/></svg> Memuat profil @' + escapeHTML(username) + '...</p></div>';
  try {
    var res = await fetch('/api/profile?username=' + encodeURIComponent(username) + '&max_results=5&sort=' + encodeURIComponent(sort) + '&date_range=' + encodeURIComponent(dateRange));
    if(!res.ok) throw new Error("Gagal mengambil profil.");
    var data = await res.json();
    var results = data.results || [];
    document.getElementById('profileResults').innerHTML = results.map(rowResult).join('') || '<div class="empty-state" style="padding:32px;"><p>Tidak ada hasil profil.</p></div>';
    document.getElementById('profileAnalytics').innerHTML = '<div style="font-family:DM Serif Display,serif;font-size:20px;color:var(--accent);margin-bottom:12px;">Intelligence</div><div style="padding:16px;border-radius:14px;background:var(--bg-white);border:1px solid var(--line);"><p style="font-size:14px;color:var(--ink);font-weight:700;margin-bottom:4px;">' + results.length + ' konten dianalisis</p><p style="font-size:13px;color:var(--soft);">dari @<strong>' + escapeHTML(username) + '</strong></p></div>';
  } catch(err) {
    document.getElementById('profileAnalytics').innerHTML = '<div class="empty-state"><p style="color:var(--accent);">' + err.message + '</p></div>';
  }
}

/* ===== COMMENTS ===== */
async function loadComments() {
  var videoUrl = document.getElementById('commentsUrl').value.trim();
  var max = document.getElementById('commentsMax').value || '3';
  document.getElementById('commentsMeta').innerHTML = '<span style="display:inline-flex;align-items:center;gap:6px;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="animation:spin 1s linear infinite;"><path d="M21 12a9 9 0 11-6.219-8.56"/></svg> Mengekstrak komentar...</span>';
  try {
    var res = await fetch('/api/comments?video_url=' + encodeURIComponent(videoUrl) + '&max_comments=' + encodeURIComponent(max));
    var data = await res.json();
    document.getElementById('commentsMeta').innerHTML = '<span style="display:inline-flex;align-items:center;gap:6px;color:var(--green);"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg> ' + (data.total || 0) + ' komentar diekstrak' + (data.video_comment_count != null ? ' (total: ' + data.video_comment_count + ')' : '') + '</span>';
    document.getElementById('commentsResults').innerHTML = (data.comments || []).map(function(c) {
      return '<div class="comment-card"><strong>' + escapeHTML(c.nickname || c.user || 'User') + '</strong><p>' + escapeHTML(c.text || '') + '</p></div>';
    }).join('') || '<div class="empty-state" style="padding:32px;"><p>Belum ada komentar.</p></div>';
  } catch(err) {
    document.getElementById('commentsMeta').innerHTML = '<span style="color:var(--accent);">Ekstraksi gagal.</span>';
  }
}

/* ===== SAVED / PLAYLIST ===== */
var SAVED_PLAYLISTS = [];
var SAVED_TAB_READY = false;

function playlistOptionsHtml(playlists) {
  return playlists.map(function(p) {
    return '<option value="' + escapeHTML(p.id || '') + '">' + escapeHTML(p.name || 'Playlist') + '</option>';
  }).join('');
}

async function loadPlaylists() {
  var res = await fetch('/api/saved/playlists');
  if (!res.ok) {
    throw new Error('Gagal memuat playlist. Pastikan kamu sudah login.');
  }
  var data = await res.json();
  SAVED_PLAYLISTS = data.playlists || [];
  if (!SAVED_PLAYLISTS.length) {
    var createRes = await fetch('/api/saved/playlists', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'Favorit' })
    });
    if (createRes.ok) {
      var created = await createRes.json();
      if (created && created.playlist) SAVED_PLAYLISTS = [created.playlist];
    }
  }
  var sel = document.getElementById('savedPlaylistSelect');
  if (!sel) return;
  sel.innerHTML = playlistOptionsHtml(SAVED_PLAYLISTS);
}

function renderSavedItems(items) {
  var root = document.getElementById('savedResults');
  if (!root) return;
  if (!items.length) {
    root.innerHTML = '<div class="empty-state" style="padding:28px;"><p>Belum ada item tersimpan. Klik tombol <strong>Save</strong> di hasil riset.</p></div>';
    return;
  }
  root.innerHTML = items.map(function(item) {
    var title = escapeHTML(item.title || 'Tanpa judul');
    var platform = escapeHTML(item.platform || 'N/A');
    var author = escapeHTML(item.author || '');
    var videoUrl = escapeHTML(item.video_url || '');
    var createdAt = escapeHTML((item.created_at || '').replace('T', ' ').slice(0, 19));
    return '<div class="saved-item">' +
      '<div class="saved-item-top">' +
        '<div style="min-width:0;flex:1;">' +
          (videoUrl ? '<a href="' + videoUrl + '" target="_blank" class="result-title" style="margin:0 0 6px 0;">' + title + '</a>' : '<div class="result-title" style="margin:0 0 6px 0;">' + title + '</div>') +
          '<div class="saved-item-meta">' +
            '<span class="result-badge">' + platform + '</span>' +
            (author ? '<span>@' + author + '</span>' : '') +
            (createdAt ? '<span>' + createdAt + '</span>' : '') +
          '</div>' +
        '</div>' +
        '<button type="button" class="btn-dl saved-remove-btn" data-id="' + escapeHTML(item.id || '') + '">Hapus</button>' +
      '</div>' +
      '<div class="result-stats">' +
        '<span class="result-stat"><strong>' + formatNum(item.views) + '</strong> views</span>' +
        '<span class="result-stat"><strong>' + formatNum(item.likes) + '</strong> likes</span>' +
        '<span class="result-stat"><strong>' + formatNum(item.comments) + '</strong> comments</span>' +
      '</div>' +
    '</div>';
  }).join('');
  root.querySelectorAll('.saved-remove-btn').forEach(function(btn) {
    btn.addEventListener('click', function() {
      removeSavedItem(btn.dataset.id);
    });
  });
}

async function loadSavedItems() {
  var meta = document.getElementById('savedMeta');
  var sel = document.getElementById('savedPlaylistSelect');
  if (!meta || !sel) return;
  meta.textContent = 'Memuat saved items...';
  var playlistId = sel.value;
  var url = '/api/saved/items' + (playlistId ? ('?playlist_id=' + encodeURIComponent(playlistId)) : '');
  var res = await fetch(url);
  var data = await res.json();
  if (!res.ok) {
    throw new Error(data.error || 'Gagal memuat saved items.');
  }
  var items = data.items || [];
  meta.textContent = items.length + ' item tersimpan';
  renderSavedItems(items);
}

async function loadSavedTab() {
  try {
    await loadPlaylists();
    await loadSavedItems();
    if (!SAVED_TAB_READY) {
      var sel = document.getElementById('savedPlaylistSelect');
      var refreshBtn = document.getElementById('refreshSavedBtn');
      var createBtn = document.getElementById('createPlaylistBtn');
      if (sel) sel.addEventListener('change', function() { loadSavedItems().catch(function(){}); });
      if (refreshBtn) refreshBtn.addEventListener('click', function() { loadSavedItems().catch(function(){}); });
      if (createBtn) {
        createBtn.addEventListener('click', async function() {
          var name = prompt('Nama playlist baru:', 'Ide Konten');
          if (!name) return;
          var res = await fetch('/api/saved/playlists', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name })
          });
          var data = await res.json();
          if (!res.ok) {
            alert(data.error || 'Gagal membuat playlist.');
            return;
          }
          await loadPlaylists();
          var selNow = document.getElementById('savedPlaylistSelect');
          if (selNow && data.playlist && data.playlist.id) {
            selNow.value = data.playlist.id;
          }
          await loadSavedItems();
        });
      }
      SAVED_TAB_READY = true;
    }
  } catch (err) {
    var meta = document.getElementById('savedMeta');
    if (meta) meta.textContent = err.message || 'Gagal memuat saved tab.';
  }
}

async function saveResultById(rid, btn) {
  try {
    var item = RESULT_CACHE[rid];
    if (!item) {
      alert('Data hasil tidak ditemukan. Coba scan ulang.');
      return;
    }
    await loadPlaylists();
    var sel = document.getElementById('savedPlaylistSelect');
    var playlistId = sel && sel.value ? sel.value : (SAVED_PLAYLISTS[0] && SAVED_PLAYLISTS[0].id ? SAVED_PLAYLISTS[0].id : null);
    var res = await fetch('/api/saved/items', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ playlist_id: playlistId, item: item, source: 'search' })
    });
    var data = await res.json();
    if (!res.ok) {
      alert(data.error || 'Gagal menyimpan item.');
      return;
    }
    if (btn) {
      btn.classList.add('saved');
      btn.textContent = data.already_saved ? 'Saved ✓' : 'Tersimpan ✓';
    }
  } catch (err) {
    alert(err.message || 'Gagal menyimpan item.');
  }
}

async function removeSavedItem(itemId) {
  if (!itemId) return;
  if (!confirm('Hapus item ini dari saved list?')) return;
  var res = await fetch('/api/saved/items/' + encodeURIComponent(itemId), { method: 'DELETE' });
  if (!res.ok) {
    var data = await res.json();
    alert((data && data.error) || 'Gagal menghapus item.');
    return;
  }
  await loadSavedItems();
}

document.getElementById('searchBtn').addEventListener('click', runSearch);
document.getElementById('profileLoadBtn').addEventListener('click', loadProfile);
document.getElementById('commentsLoadBtn').addEventListener('click', loadComments);

/* ===== QUOTA ===== */
async function loadQuota() {
  try {
    var res = await fetch('/api/account/usage');
    if (!res.ok) return;
    var data = await res.json();
    if (!data.configured || !data.profile) return;
    var banner = document.getElementById('quotaBanner');
    banner.style.display = '';
    var plan = data.plan || {};
    var profile = data.profile || {};
    var tier = plan.code || 'free';
    document.getElementById('quotaPlanName').textContent = plan.name || 'Free';
    document.getElementById('quotaTierBadge').textContent = tier.toUpperCase();
    var dailyLimit = plan.daily_search_limit || 0;
    var left = profile.daily_searches_left != null ? profile.daily_searches_left : 0;
    if (dailyLimit > 0) {
      var used = dailyLimit - left;
      var pct = Math.min(100, Math.round((used / dailyLimit) * 100));
      document.getElementById('quotaBar').style.width = pct + '%';
      if (pct > 80) document.getElementById('quotaBar').style.background = 'linear-gradient(90deg,#c0391b,#ef5a29)';
      document.getElementById('quotaText').textContent = left + ' / ' + dailyLimit + ' pencarian tersisa hari ini';
    } else {
      document.getElementById('quotaBar').style.width = '15%';
      document.getElementById('quotaText').textContent = 'Unlimited pencarian';
    }
    var upgradeBtn = document.getElementById('quotaUpgradeBtn');
    if (tier === 'free' || tier === 'starter') {
      upgradeBtn.style.display = '';
    } else {
      upgradeBtn.style.display = 'none';
      var card = document.getElementById('sidebarUpgradeCard');
      if (card) card.style.display = 'none';
    }
    if (data.user && data.user.email) {
      var av = document.querySelector('.header-avatar');
      if (av) av.textContent = data.user.email[0].toUpperCase();
    }
  } catch(e) { console.warn('[Sinyal] quota load failed:', e); }
}
loadQuota();

/* ===== PLATFORM GATING ===== */
async function gatePlatforms() {
  try {
    var res = await fetch('/api/account/usage');
    if (!res.ok) return;
    var data = await res.json();
    if (!data.configured || !data.plan) return;
    var allowed = data.plan.allowed_platforms || ['tiktok'];
    document.querySelectorAll('#platformChips input[type="checkbox"]').forEach(function(cb) {
      if (!allowed.includes(cb.value)) {
        cb.checked = false;
        cb.disabled = true;
        cb.parentElement.style.opacity = '0.4';
        cb.parentElement.title = 'Upgrade untuk platform ini';
      }
    });
  } catch(e) {}
}
gatePlatforms();

/* ===== DASHBOARD INTEL ===== */
var _dashboardIntelLoadedKey = '';
var _intelDefaultDate = new Date().toISOString().slice(0, 10);

function setIntelInputsByPreset(preset) {
  var startEl = document.getElementById('intelStartDate');
  var endEl = document.getElementById('intelEndDate');
  if (!startEl || !endEl) return;
  var end = new Date(_intelDefaultDate + 'T00:00:00');
  var start = new Date(end);
  if (preset === '7d') start.setDate(start.getDate() - 6);
  else if (preset === '30d') start.setDate(start.getDate() - 29);
  var format = function(date) { return date.toISOString().slice(0, 10); };
  if (preset === 'custom') {
    if (!startEl.value) startEl.value = format(start);
    if (!endEl.value) endEl.value = format(end);
  } else {
    startEl.value = format(start);
    endEl.value = format(end);
  }
  startEl.disabled = preset !== 'custom';
  endEl.disabled = preset !== 'custom';
}

function getIntelQueryParams() {
  var presetEl = document.getElementById('intelPreset');
  var startEl = document.getElementById('intelStartDate');
  var endEl = document.getElementById('intelEndDate');
  var preset = presetEl ? presetEl.value : 'today';
  var startDate = startEl ? startEl.value : '';
  var endDate = endEl ? endEl.value : '';
  if (!startDate) startDate = _intelDefaultDate;
  if (!endDate) endDate = startDate;
  var params = new URLSearchParams();
  params.set('limit', '6');
  params.set('preset', preset);
  params.set('start_date', startDate);
  params.set('end_date', endDate);
  return params;
}

function updateIntelFilterMeta() {
  var meta = document.getElementById('intelFilterMeta');
  if (!meta) return;
  var params = getIntelQueryParams();
  var preset = params.get('preset');
  var startDate = params.get('start_date');
  var endDate = params.get('end_date');
  var label = 'Menampilkan data untuk ' + startDate;
  if (startDate !== endDate) label = 'Menampilkan data ' + startDate + ' s/d ' + endDate;
  if (preset === '7d') label = 'Menampilkan data 7 hari terakhir';
  if (preset === '30d') label = 'Menampilkan data 30 hari terakhir';
  meta.textContent = label + '.';
}

function fmtIntelValue(v) {
  if (v == null || v === '') return '-';
  if (typeof v === 'number') return Number(v).toLocaleString('id-ID');
  return String(v);
}

function intelMetric(label, value) {
  return '<div class="intel-metric"><span>' + escapeHTML(label) + '</span><strong>' + escapeHTML(fmtIntelValue(value)) + '</strong></div>';
}

function intelChips(values) {
  var items = (values || []).filter(function(v) {
    if (!v && v !== 0) return false;
    if (typeof v === 'object') return false;
    var s = String(v).trim();
    if (!s) return false;
    if (/^\\d{5,}$/.test(s)) return false;
    return true;
  });
  if (!items.length) return '';
  return '<div class="intel-chips">' + items.map(function(item) {
    return '<span class="intel-chip">' + escapeHTML(String(item)) + '</span>';
  }).join('') + '</div>';
}

function renderIntelProduct(item, index) {
  var chips = [
    item.delivery_type ? '🚚 ' + item.delivery_type : '',
    item.commission_rate ? '💰 ' + item.commission_rate : '',
    item.product_rating ? '⭐ ' + item.product_rating : '',
    item.creator_num ? item.creator_num + ' creator' : '',
    item.is_tokopedia ? 'Tokopedia' : '',
  ];
  return '<div class="intel-card">' +
    '<div style="display:flex;gap:12px;align-items:flex-start;">' +
      '<div class="intel-rank">#' + (index + 1) + '</div>' +
      '<div style="flex:1;min-width:0;">' +
        '<p class="intel-title">' + escapeHTML(item.product_title || ('Product ' + (index + 1))) + '</p>' +
        '<div class="intel-sub">ID Produk: ' + escapeHTML(item.id || '-') + ' · Launch: ' + escapeHTML(item.launch_date || '-') + '</div>' +
      '</div>' +
    '</div>' +
    '<div class="intel-metrics">' +
      intelMetric('GMV', item.revenue) +
      intelMetric('Terjual', item.sale) +
      intelMetric('Harga Avg', item.unit_price) +
      intelMetric('Video Revenue', item.video_revenue) +
      intelMetric('Live Revenue', item.live_revenue) +
      intelMetric('Price Range', (item.min_real_price || '-') + ' — ' + (item.max_real_price || '-')) +
    '</div>' +
    intelChips(chips) +
  '</div>';
}

function renderIntelCreator(item, index) {
  var engRate = item.video_engagement_rate;
  var chips = [
    typeof engRate === 'number' ? (engRate < 1 ? (engRate * 100).toFixed(1) + '%' : engRate.toFixed(1) + '%') : (engRate || ''),
    item.creatorDebut ? 'Debut ' + item.creatorDebut : '',
    item.seller_type || '',
    item.region || '',
  ];
  return '<div class="intel-card">' +
    '<div style="display:flex;gap:12px;align-items:flex-start;">' +
      '<div class="intel-rank">#' + (index + 1) + '</div>' +
      '<div style="flex:1;min-width:0;">' +
        '<p class="intel-title">' + escapeHTML(item.nickname || ('Creator ' + (index + 1))) + '</p>' +
        '<div class="intel-handle">@' + escapeHTML(item.handle || '-') + '</div>' +
        (item.signature ? '<div class="intel-signature">' + escapeHTML(item.signature) + '</div>' : '') +
      '</div>' +
    '</div>' +
    '<div class="intel-metrics">' +
      intelMetric('Revenue', item.revenue) +
      intelMetric('Sale', item.sale) +
      intelMetric('Followers', item.followers) +
      intelMetric('Views', item.views) +
      intelMetric('New Followers', item.new_followers) +
      intelMetric('Avg Price', item.unit_price) +
    '</div>' +
    intelChips(chips) +
  '</div>';
}

function renderIntelRows(items, type) {
  if (!items || !items.length) {
    return '<div class="intel-empty">Belum ada data untuk rentang tanggal ini.</div>';
  }
  return '<div class="intel-mini-table">' + items.map(function(item, index) {
    var title = type === 'shop'
      ? (item.name || item.shop_name || ('Shop #' + (index + 1)))
      : (item.product_title || ('Product #' + (index + 1)));
    var meta = type === 'shop'
      ? [item.seller_type, item.region, item.is_tokopedia ? 'Tokopedia' : ''].filter(Boolean).join(' · ')
      : [item.unit_price, item.delivery_type, item.commission_rate].filter(Boolean).join(' · ');
    var value = type === 'shop' ? item.revenue : item.sale;
    var label = type === 'shop' ? 'Revenue' : 'Terjual';
    return '<div class="intel-row">' +
      '<div style="flex:1;min-width:0;">' +
        '<strong>#' + (index + 1) + ' ' + escapeHTML(title) + '</strong>' +
        '<span>' + escapeHTML(meta || '-') + '</span>' +
      '</div>' +
      '<div style="text-align:right;min-width:110px;">' +
        '<strong>' + escapeHTML(fmtIntelValue(value)) + '</strong>' +
        '<span>' + label + '</span>' +
      '</div>' +
    '</div>';
  }).join('') + '</div>';
}

async function loadDashboardIntel(force) {
  var params = getIntelQueryParams();
  var requestKey = params.toString();
  if (_dashboardIntelLoadedKey === requestKey && !force) return;
  var container = document.getElementById('dashboardActivity');
  if (!container) return;
  updateIntelFilterMeta();
  container.innerHTML = '<div class="intel-panel"><div class="section-label">Market Pulse Indonesia</div><div class="intel-empty">Mengambil data trending Indonesia...</div></div>';
  try {
    var res = await fetch('/api/dashboard/trending?' + params.toString());
    var data = await res.json();
    if (!res.ok) {
      if (res.status === 403 && data.upgrade_url) {
        container.innerHTML = '<div class="intel-panel"><div class="section-label">Market Pulse Indonesia</div><div class="intel-empty">' +
          escapeHTML(data.error || 'Upgrade dibutuhkan untuk melihat market intel.') +
          '<br><br><a class="btn-action" href="' + escapeHTML(data.upgrade_url) + '" style="text-decoration:none;display:inline-flex;">Upgrade untuk buka data trending</a></div></div>';
        return;
      }
      container.innerHTML = '<div class="intel-panel"><div class="section-label">Market Pulse Indonesia</div><div class="intel-empty">' + escapeHTML(data.error || 'Gagal memuat dashboard intel.') + '</div></div>';
      return;
    }

    var summary = data.summary || {};
    var windowInfo = data.window || {};
    var region = data.region || {};
    var note = data.note || '';
    var products = data.products_by_revenue || [];
    var creators = data.creators_by_revenue || [];
    var productsBySale = data.products_by_sale || [];
    var shops = data.shops_by_revenue || [];
    container.innerHTML = '' +
      '<div class="intel-board">' +
        '<div class="intel-panel">' +
          '<div class="intel-header">' +
            '<div><h3>Market Pulse ' + escapeHTML(region.label || 'Indonesia') + '</h3><div class="intel-sub">Trending TikTok Shop ' + escapeHTML(windowInfo.label || 'hari ini') + ' dengan region default ' + escapeHTML(region.label || 'Indonesia') + '.</div></div>' +
            '<div class="intel-badges">' +
              '<div class="intel-badge">Region ' + escapeHTML(region.code || 'id').toUpperCase() + '</div>' +
              '<div class="intel-badge">Range ' + escapeHTML((windowInfo.start_date || '-') + (windowInfo.end_date && windowInfo.end_date !== windowInfo.start_date ? ' → ' + windowInfo.end_date : '')) + '</div>' +
              (data.cached ? '<div class="intel-badge">Cache</div>' : '') +
              (data.stale ? '<div class="intel-badge">Stale</div>' : '') +
            '</div>' +
          '</div>' +
          (note ? '<div class="intel-empty" style="margin-bottom:14px;">' + escapeHTML(note) + '</div>' : '') +
          '<div class="intel-summary-grid">' +
            '<div class="intel-summary-card"><span>Top Product GMV</span><strong>' + escapeHTML(fmtIntelValue(summary.top_product_revenue)) + '</strong><small>' + escapeHTML(products[0] ? (products[0].product_title || '-') : '-') + '</small></div>' +
            '<div class="intel-summary-card"><span>Top Product Sold</span><strong>' + escapeHTML(fmtIntelValue(summary.top_product_sale)) + '</strong><small>' + escapeHTML(productsBySale[0] ? (productsBySale[0].product_title || '-') : '-') + '</small></div>' +
            '<div class="intel-summary-card"><span>Top Creator</span><strong>' + escapeHTML(fmtIntelValue(summary.top_creator_revenue)) + '</strong><small>' + escapeHTML(creators[0] ? ((creators[0].nickname || '-') + ' @' + (creators[0].handle || '-')) : '-') + '</small></div>' +
            '<div class="intel-summary-card"><span>Top Shop</span><strong>' + escapeHTML(fmtIntelValue(summary.top_shop_revenue)) + '</strong><small>' + escapeHTML(shops[0] ? (shops[0].name || shops[0].shop_name || '-') : '-') + '</small></div>' +
          '</div>' +
        '</div>' +
        '<div class="intel-grid">' +
          '<div class="intel-panel"><div class="intel-header"><div><h3>Produk Trending</h3><div class="intel-sub">Urut berdasarkan revenue / GMV untuk rentang tanggal terpilih.</div></div></div><div class="intel-list-grid">' +
            (products.length ? products.map(renderIntelProduct).join('') : '<div class="intel-empty">Belum ada produk trending untuk rentang tanggal ini.</div>') +
          '</div></div>' +
          '<div class="intel-panel"><div class="intel-header"><div><h3>Creator Trending</h3><div class="intel-sub">Creator dengan revenue tertinggi untuk rentang tanggal terpilih.</div></div></div><div class="intel-list-grid">' +
            (creators.length ? creators.map(renderIntelCreator).join('') : '<div class="intel-empty">Belum ada creator trending untuk rentang tanggal ini.</div>') +
          '</div></div>' +
        '</div>' +
        '<div class="intel-grid">' +
          '<div class="intel-panel"><div class="intel-header"><div><h3>Produk Paling Laris</h3><div class="intel-sub">Urut berdasarkan jumlah unit terjual untuk rentang tanggal terpilih.</div></div></div>' +
            renderIntelRows(productsBySale.slice(0, 6), 'product') +
          '</div>' +
          '<div class="intel-panel"><div class="intel-header"><div><h3>Shop Tertinggi</h3><div class="intel-sub">Shop dengan revenue tertinggi untuk region Indonesia pada rentang tanggal terpilih.</div></div></div>' +
            renderIntelRows(shops.slice(0, 6), 'shop') +
          '</div>' +
        '</div>' +
      '</div>';
    _dashboardIntelLoadedKey = requestKey;
  } catch (e) {
    console.warn('[Sinyal] dashboard intel load failed:', e);
    container.innerHTML = '<div class="intel-panel"><div class="section-label">Market Pulse Indonesia</div><div class="intel-empty">Gagal memuat data trending Indonesia.</div></div>';
  }

function getPlatformLabel(platform) {
  return {
    tiktok: 'TikTok',
    youtube: 'YouTube',
    instagram: 'Instagram',
    twitter: 'X',
    facebook: 'Facebook'
  }[platform] || platform;
}

function renderPlatformBreakdown(breakdown) {
  if (!breakdown) return '';
  var entries = Object.entries(breakdown);
  if (!entries.length) return '';
  return '<div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:8px;">' + entries.map(function(entry) {
    var platform = entry[0];
    var info = entry[1] || {};
    var count = Number(info.results || 0);
    var errorCount = Array.isArray(info.errors) ? info.errors.length : 0;
    var bg = count > 0 ? 'rgba(40,95,88,0.08)' : 'rgba(239,90,41,0.08)';
    var color = count > 0 ? 'var(--green)' : 'var(--accent)';
    var suffix = errorCount ? ' · ' + errorCount + ' error' : '';
    return '<span style="display:inline-flex;align-items:center;padding:6px 10px;border-radius:999px;background:' + bg + ';color:' + color + ';font-size:11px;font-weight:800;">' +
      escapeHTML(getPlatformLabel(platform) + ': ' + count + suffix) +
    '</span>';
  }).join('') + '</div>';
}
}
document.getElementById('intelPreset').addEventListener('change', function() {
  setIntelInputsByPreset(this.value);
  updateIntelFilterMeta();
});
document.getElementById('intelStartDate').addEventListener('change', function() {
  document.getElementById('intelPreset').value = 'custom';
  setIntelInputsByPreset('custom');
  updateIntelFilterMeta();
});
document.getElementById('intelEndDate').addEventListener('change', function() {
  document.getElementById('intelPreset').value = 'custom';
  setIntelInputsByPreset('custom');
  updateIntelFilterMeta();
});
document.getElementById('intelApplyBtn').addEventListener('click', function() {
  loadDashboardIntel(true);
});
setIntelInputsByPreset('today');
updateIntelFilterMeta();
loadDashboardIntel();

/* ===== AFFILIATE ===== */
function fmtIDR(n) {
  if (!n) return 'Rp0';
  return 'Rp' + Number(n).toLocaleString('id-ID');
}

var _affLoaded = false;

async function loadAffiliateTab() {
  try {
    var res = await fetch('/api/affiliate/me');
    if (!res.ok) return;
    var data = await res.json();
    var aff = data.affiliate;
    if (!aff) {
      document.getElementById('affActivateCard').style.display = '';
      document.getElementById('affDashboard').style.display = 'none';
      return;
    }
    document.getElementById('affActivateCard').style.display = 'none';
    document.getElementById('affDashboard').style.display = '';
    document.getElementById('affRefLink').value = data.referral_url || '';
    document.getElementById('affTotalRefs').textContent = aff.referral_count || 0;
    document.getElementById('affConvertedRefs').textContent = aff.paid_referral_count || 0;
    document.getElementById('affLifetimeEarnings').textContent = fmtIDR(aff.lifetime_earnings);
    document.getElementById('affPendingBalance').textContent = fmtIDR(aff.pending_balance);

    // Load payout settings
    if (aff.payout_method) {
      document.getElementById('affPayoutMethod').value = aff.payout_method;
      togglePayoutFields();
      var detail = aff.payout_detail || {};
      if (detail.bank_name) document.getElementById('affBankName').value = detail.bank_name;
      if (detail.account_number) document.getElementById('affAccountNumber').value = detail.account_number;
      if (detail.account_name) document.getElementById('affAccountName').value = detail.account_name;
      if (detail.phone) document.getElementById('affAccountNumber').value = detail.phone;
    }

    // Load referrals
    await loadAffReferrals();
    // Load payout history
    await loadAffPayouts();
    _affLoaded = true;
  } catch(e) { console.warn('[Sinyal] affiliate load failed:', e); }
}

async function activateAffiliate() {
  var btn = document.getElementById('affActivateBtn');
  btn.textContent = 'Mengaktifkan...';
  btn.disabled = true;
  try {
    var res = await fetch('/api/affiliate/activate', { method: 'POST' });
    var data = await res.json();
    if (res.ok) {
      await loadAffiliateTab();
    } else {
      alert(data.error || 'Gagal mengaktifkan affiliate.');
    }
  } catch(e) {
    alert('Terjadi kesalahan.');
  }
  btn.textContent = 'Aktifkan Affiliate \\u2192';
  btn.disabled = false;
}

function copyAffLink() {
  var input = document.getElementById('affRefLink');
  navigator.clipboard.writeText(input.value);
  var btn = event.target;
  btn.textContent = 'Copied!';
  setTimeout(function() { btn.textContent = 'Copy Link'; }, 1500);
}

function getPlatformLabel(platform) {
  return {
    tiktok: 'TikTok',
    youtube: 'YouTube',
    instagram: 'Instagram',
    twitter: 'X',
    facebook: 'Facebook'
  }[platform] || platform;
}

function renderPlatformBreakdown(breakdown) {
  if (!breakdown) return '';
  var entries = Object.entries(breakdown);
  if (!entries.length) return '';
  return '<div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:8px;">' + entries.map(function(entry) {
    var platform = entry[0];
    var info = entry[1] || {};
    var count = Number(info.results || 0);
    var errorCount = Array.isArray(info.errors) ? info.errors.length : 0;
    var bg = count > 0 ? 'rgba(40,95,88,0.08)' : 'rgba(239,90,41,0.08)';
    var color = count > 0 ? 'var(--green)' : 'var(--accent)';
    var suffix = errorCount ? ' · ' + errorCount + ' error' : '';
    return '<span style="display:inline-flex;align-items:center;padding:6px 10px;border-radius:999px;background:' + bg + ';color:' + color + ';font-size:11px;font-weight:800;">' +
      escapeHTML(getPlatformLabel(platform) + ': ' + count + suffix) +
    '</span>';
  }).join('') + '</div>';
}

function togglePayoutFields() {
  var method = document.getElementById('affPayoutMethod').value;
  var bankFields = document.getElementById('affBankFields');
  var accountFields = document.getElementById('affAccountFields');
  var nameField = document.getElementById('affAccountNameField');
  if (method === 'bank_transfer') {
    bankFields.style.display = '';
    accountFields.style.display = '';
    nameField.style.display = '';
    document.getElementById('affAccountNumber').placeholder = '1234567890';
  } else if (method === 'ewallet') {
    bankFields.style.display = 'none';
    accountFields.style.display = '';
    nameField.style.display = '';
    document.getElementById('affAccountNumber').placeholder = '08xxxxxxxxxx';
  } else {
    bankFields.style.display = 'none';
    accountFields.style.display = 'none';
    nameField.style.display = 'none';
  }
}
document.getElementById('affPayoutMethod').addEventListener('change', togglePayoutFields);

async function savePayoutSettings() {
  var method = document.getElementById('affPayoutMethod').value;
  if (!method) { alert('Pilih metode payout dulu.'); return; }
  var detail = {};
  if (method === 'bank_transfer') {
    detail.bank_name = document.getElementById('affBankName').value.trim();
    detail.account_number = document.getElementById('affAccountNumber').value.trim();
    detail.account_name = document.getElementById('affAccountName').value.trim();
    if (!detail.bank_name || !detail.account_number || !detail.account_name) {
      alert('Lengkapi semua field bank.'); return;
    }
  } else if (method === 'ewallet') {
    detail.phone = document.getElementById('affAccountNumber').value.trim();
    detail.account_name = document.getElementById('affAccountName').value.trim();
    if (!detail.phone || !detail.account_name) {
      alert('Lengkapi nomor HP dan nama pemilik.'); return;
    }
  }
  try {
    var res = await fetch('/api/affiliate/payout-settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ payout_method: method, payout_detail: detail }),
    });
    var data = await res.json();
    if (res.ok) {
      alert('Pengaturan payout berhasil disimpan.');
    } else {
      alert(data.error || 'Gagal menyimpan pengaturan.');
    }
  } catch(e) { alert('Terjadi kesalahan.'); }
}

async function requestPayout() {
  if (!confirm('Yakin mau tarik saldo sekarang?')) return;
  try {
    var res = await fetch('/api/affiliate/request-payout', { method: 'POST' });
    var data = await res.json();
    if (res.ok) {
      alert(data.message || 'Request payout berhasil!');
      await loadAffiliateTab();
    } else {
      alert(data.error || 'Gagal request payout.');
    }
  } catch(e) { alert('Terjadi kesalahan.'); }
}

async function loadAffReferrals() {
  try {
    var res = await fetch('/api/affiliate/referrals');
    if (!res.ok) return;
    var data = await res.json();
    var refs = data.referrals || [];
    var container = document.getElementById('affReferralList');
    if (!refs.length) {
      container.innerHTML = '<p style="color:var(--muted);font-size:13px;">Belum ada referral.</p>';
      return;
    }
    container.innerHTML = refs.map(function(r) {
      var statusColor = r.status === 'converted' ? 'var(--green)' : 'var(--muted)';
      var statusLabel = r.status === 'converted' ? '\\u2713 Converted' : '\\u23F3 Signed up';
      var dateStr = r.signed_up_at ? new Date(r.signed_up_at).toLocaleDateString('id-ID', { day: 'numeric', month: 'short', year: 'numeric' }) : '-';
      return '<div style="display:flex;justify-content:space-between;align-items:center;padding:12px 14px;border-radius:14px;background:rgba(255,255,255,0.7);border:1px solid var(--line);">' +
        '<div>' +
          '<div style="font-size:14px;font-weight:700;">' + escapeHTML(r.referred_email || 'Unknown') + '</div>' +
          '<div style="font-size:12px;color:var(--muted);margin-top:2px;">Daftar: ' + dateStr + (r.converted_plan ? ' \\u2022 Paket: ' + escapeHTML(r.converted_plan) : '') + '</div>' +
        '</div>' +
        '<div style="text-align:right;">' +
          '<div style="font-size:12px;font-weight:800;color:' + statusColor + ';">' + statusLabel + '</div>' +
          (r.commission_amount ? '<div style="font-size:13px;font-weight:800;color:var(--accent);margin-top:2px;">+' + fmtIDR(r.commission_amount) + '</div>' : '') +
        '</div>' +
      '</div>';
    }).join('');
  } catch(e) {}
}

async function loadAffPayouts() {
  try {
    var res = await fetch('/api/affiliate/payouts');
    if (!res.ok) return;
    var data = await res.json();
    var payouts = data.payouts || [];
    if (data.min_payout) {
      document.getElementById('affMinPayout').textContent = fmtIDR(data.min_payout);
    }
    var container = document.getElementById('affPayoutHistory');
    if (!payouts.length) {
      container.innerHTML = '<p style="color:var(--muted);font-size:13px;">Belum ada payout.</p>';
      return;
    }
    var statusMap = { pending: '\\u23F3 Pending', processing: '\\u2699 Proses', completed: '\\u2705 Selesai', rejected: '\\u274C Ditolak' };
    container.innerHTML = payouts.map(function(p) {
      var dateStr = p.requested_at ? new Date(p.requested_at).toLocaleDateString('id-ID', { day: 'numeric', month: 'short', year: 'numeric' }) : '-';
      return '<div style="display:flex;justify-content:space-between;align-items:center;padding:12px 14px;border-radius:14px;background:rgba(255,255,255,0.7);border:1px solid var(--line);">' +
        '<div>' +
          '<div style="font-size:14px;font-weight:800;">' + fmtIDR(p.amount) + '</div>' +
          '<div style="font-size:12px;color:var(--muted);margin-top:2px;">' + dateStr + ' \\u2022 ' + escapeHTML(p.payout_method || '-') + '</div>' +
        '</div>' +
        '<div style="font-size:12px;font-weight:800;">' + (statusMap[p.status] || p.status) + '</div>' +
      '</div>';
    }).join('');
  } catch(e) {}
}

/* ===== SIGNOUT ===== */
async function doSignout() {
  try { await fetch('/api/auth/signout', { method: 'POST' }); } catch(e) {}
  document.cookie = 'sinyal_access_token=; Max-Age=0; path=/';
  document.cookie = 'sinyal_refresh_token=; Max-Age=0; path=/';
  window.location.href = '/signin';
}

/* ===== PROFILE FEED FILTER ===== */
document.getElementById('profileFeedSearch').addEventListener('input', function() {
  var query = this.value.toLowerCase().trim();
  var cards = document.querySelectorAll('#profileResults > div');
  cards.forEach(function(card) {
    card.style.display = card.textContent.toLowerCase().includes(query) || !query ? '' : 'none';
  });
});
</script>
<style>@keyframes spin{to{transform:rotate(360deg)}}@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}</style>
</body>
</html>
"""


# ═══════════════════════════════════════════════════════════
# ADMIN — hidden dashboard
# ═══════════════════════════════════════════════════════════
import hashlib as _hashlib
import secrets as _secrets2

ADMIN_SESSION_TTL = 60 * 60 * 8  # 8 hours


def _make_admin_token() -> str:
    return _secrets2.token_hex(32)


def _check_admin(request: Request) -> bool:
    token = request.cookies.get(ADMIN_COOKIE_NAME)
    if not token:
        return False
    exp = _admin_sessions.get(token)
    if not exp or time.time() > exp:
        _admin_sessions.pop(token, None)
        return False
    return True


_ADMIN_LOGIN_HTML = """
<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Admin Login</title>
<style>
*{box-sizing:border-box;margin:0;padding:0;}
body{font-family:'Segoe UI',sans-serif;background:#0f0f0f;color:#e0e0e0;min-height:100vh;display:flex;align-items:center;justify-content:center;}
.card{background:#1a1a1a;border:1px solid #2a2a2a;border-radius:16px;padding:40px 36px;width:100%;max-width:380px;box-shadow:0 24px 64px rgba(0,0,0,0.5);}
h1{font-size:22px;font-weight:700;margin-bottom:4px;letter-spacing:-0.02em;}
.sub{font-size:13px;color:#666;margin-bottom:28px;}
.field{margin-bottom:16px;}
.field label{display:block;font-size:12px;font-weight:600;color:#888;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:6px;}
.field input{width:100%;padding:11px 14px;border-radius:10px;border:1px solid #2a2a2a;background:#111;color:#e0e0e0;font-size:14px;font-family:inherit;outline:none;transition:border-color .15s;}
.field input:focus{border-color:#ef5a29;}
.btn{width:100%;padding:13px;border-radius:10px;border:none;background:linear-gradient(135deg,#c0391b,#ef5a29);color:#fff;font-weight:800;font-size:14px;cursor:pointer;margin-top:6px;transition:opacity .15s;}
.btn:hover{opacity:.9;}
.err{background:rgba(239,90,41,0.12);border:1px solid rgba(239,90,41,0.3);border-radius:10px;padding:10px 14px;font-size:13px;color:#ef5a29;margin-bottom:16px;}
</style>
</head>
<body>
<div class="card">
  <h1>&#128274; Admin</h1>
  <p class="sub">Sinyal Dashboard</p>
  {error_block}
  <form method="POST" action="/admin/login">
    <div class="field"><label>Username</label><input name="username" type="text" autocomplete="username" required autofocus></div>
    <div class="field"><label>Password</label><input name="password" type="password" autocomplete="current-password" required></div>
    <button class="btn" type="submit">Masuk</button>
  </form>
</div>
</body></html>
"""


@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page(request: Request, error: str = ""):
    if _check_admin(request):
        return RedirectResponse("/admin", status_code=302)
    err = '<div class="err">Username atau password salah.</div>' if error else ""
    return HTMLResponse(_ADMIN_LOGIN_HTML.replace("{error_block}", err))


@app.post("/admin/login")
async def admin_login_post(request: Request):
    form = await request.form()
    username = (form.get("username") or "").strip()
    password = (form.get("password") or "")
    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        token = _make_admin_token()
        _admin_sessions[token] = time.time() + ADMIN_SESSION_TTL
        response = RedirectResponse("/admin", status_code=302)
        response.set_cookie(ADMIN_COOKIE_NAME, token, httponly=True, samesite="lax",
                            secure=COOKIE_SECURE, max_age=ADMIN_SESSION_TTL)
        return response
    return RedirectResponse("/admin/login?error=1", status_code=302)


@app.post("/admin/logout")
async def admin_logout(request: Request):
    token = request.cookies.get(ADMIN_COOKIE_NAME)
    if token:
        _admin_sessions.pop(token, None)
    response = RedirectResponse("/admin/login", status_code=302)
    response.delete_cookie(ADMIN_COOKIE_NAME)
    return response


@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    if not _check_admin(request):
        return RedirectResponse("/admin/login", status_code=302)

    # ── fetch stats from Supabase ─────────────────────────────
    now_iso = datetime.now(timezone.utc).isoformat()
    today_iso = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    week_ago_iso = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

    # Total users
    st_u, d_u, h_u = await supabase_rest_request("GET", "/rest/v1/profiles",
        params={"select": "id", "limit": "1"}, prefer="count=exact")
    total_users = int(h_u.get("content-range", "0/0").split("/")[-1] or 0)

    # New users today
    st_t, d_t, h_t = await supabase_rest_request("GET", "/rest/v1/profiles",
        params={"select": "id", "created_at": f"gte.{today_iso}", "limit": "1"}, prefer="count=exact")
    new_today = int(h_t.get("content-range", "0/0").split("/")[-1] or 0)

    # New users this week
    st_w, d_w, h_w = await supabase_rest_request("GET", "/rest/v1/profiles",
        params={"select": "id", "created_at": f"gte.{week_ago_iso}", "limit": "1"}, prefer="count=exact")
    new_week = int(h_w.get("content-range", "0/0").split("/")[-1] or 0)

    # Tier breakdown
    st_tiers, d_tiers, _ = await supabase_rest_request("GET", "/rest/v1/profiles",
        params={"select": "tier"})
    tier_counts: dict[str, int] = {}
    if isinstance(d_tiers, list):
        for row in d_tiers:
            t = row.get("tier", "free") or "free"
            tier_counts[t] = tier_counts.get(t, 0) + 1

    # Recent signups (last 10)
    st_r, d_r, _ = await supabase_rest_request("GET", "/rest/v1/profiles",
        params={"select": "id,email,tier,created_at", "order": "created_at.desc", "limit": "10"})
    recent_users = d_r if isinstance(d_r, list) else []

    # Affiliate stats
    st_af, d_af, h_af = await supabase_rest_request("GET", "/rest/v1/affiliates",
        params={"select": "id", "limit": "1"}, prefer="count=exact")
    total_affiliates = int(h_af.get("content-range", "0/0").split("/")[-1] or 0)

    st_ref, d_ref, h_ref = await supabase_rest_request("GET", "/rest/v1/affiliate_referrals",
        params={"select": "id", "limit": "1"}, prefer="count=exact")
    total_referrals = int(h_ref.get("content-range", "0/0").split("/")[-1] or 0)

    # Payout requests pending
    st_po, d_po, h_po = await supabase_rest_request("GET", "/rest/v1/affiliate_payouts",
        params={"select": "id", "status": "eq.pending", "limit": "1"}, prefer="count=exact")
    pending_payouts = int(h_po.get("content-range", "0/0").split("/")[-1] or 0)

    # Saved playlists & items
    st_pl, d_pl, h_pl = await supabase_rest_request("GET", "/rest/v1/saved_playlists",
        params={"select": "id", "limit": "1"}, prefer="count=exact")
    total_playlists = int(h_pl.get("content-range", "0/0").split("/")[-1] or 0)

    st_si, d_si, h_si = await supabase_rest_request("GET", "/rest/v1/saved_items",
        params={"select": "id", "limit": "1"}, prefer="count=exact")
    total_saved = int(h_si.get("content-range", "0/0").split("/")[-1] or 0)

    # ── build HTML ────────────────────────────────────────────
    def stat_card(icon, label, value, sub="", color="#ef5a29"):
        return f"""
        <div class="stat">
          <div class="stat-icon" style="color:{color}">{icon}</div>
          <div class="stat-val">{value}</div>
          <div class="stat-label">{label}</div>
          {f'<div class="stat-sub">{sub}</div>' if sub else ''}
        </div>"""

    tier_rows = "".join(
        f'<tr><td><span class="tier-badge tier-{t}">{t.upper()}</span></td><td>{c}</td></tr>'
        for t, c in sorted(tier_counts.items())
    )

    user_rows = ""
    for u in recent_users:
        created = u.get("created_at", "")[:10]
        email = u.get("email") or u.get("id", "")[:12] + "..."
        tier = u.get("tier") or "free"
        user_rows += f'<tr><td class="mono">{email}</td><td><span class="tier-badge tier-{tier}">{tier.upper()}</span></td><td class="mono">{created}</td></tr>'

    pending_alert = ""
    if pending_payouts > 0:
        pending_alert = f'<div class="alert">&#9888; Ada <strong>{pending_payouts}</strong> permintaan payout affiliate menunggu persetujuan.</div>'

    return HTMLResponse(f"""
<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Admin &#8212; Sinyal</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:'Segoe UI',sans-serif;background:#0f0f0f;color:#e0e0e0;min-height:100vh;}}
a{{text-decoration:none;color:inherit;}}
nav{{background:#151515;border-bottom:1px solid #222;padding:0 24px;display:flex;align-items:center;justify-content:space-between;height:56px;}}
.brand{{font-weight:800;font-size:18px;letter-spacing:-0.02em;color:#fff;}}  
.brand span{{color:#ef5a29;}}
.nav-right{{display:flex;align-items:center;gap:16px;}}
.nav-right span{{font-size:13px;color:#666;}}
.btn-sm{{padding:7px 14px;border-radius:8px;border:1px solid #333;background:transparent;color:#aaa;font-size:12px;font-weight:700;cursor:pointer;transition:all .15s;}}
.btn-sm:hover{{border-color:#ef5a29;color:#ef5a29;}}
main{{max-width:1100px;margin:0 auto;padding:28px 24px;}}
h2{{font-size:20px;font-weight:700;margin-bottom:16px;letter-spacing:-0.02em;}}
.section{{margin-bottom:32px;}}
.stats-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:12px;margin-bottom:32px;}}
.stat{{background:#1a1a1a;border:1px solid #222;border-radius:14px;padding:20px 18px;}}
.stat-icon{{font-size:22px;margin-bottom:8px;}}
.stat-val{{font-size:28px;font-weight:800;letter-spacing:-0.03em;color:#fff;}}
.stat-label{{font-size:12px;color:#666;font-weight:600;margin-top:3px;text-transform:uppercase;letter-spacing:0.04em;}}
.stat-sub{{font-size:12px;color:#ef5a29;font-weight:700;margin-top:4px;}}
.card{{background:#1a1a1a;border:1px solid #222;border-radius:14px;padding:20px 22px;}}
table{{width:100%;border-collapse:collapse;font-size:13px;}}
th{{text-align:left;padding:8px 12px;color:#666;font-size:11px;text-transform:uppercase;letter-spacing:0.06em;border-bottom:1px solid #222;font-weight:600;}}
td{{padding:10px 12px;border-bottom:1px solid #1e1e1e;color:#ccc;}}
tr:last-child td{{border-bottom:none;}}
.mono{{font-family:'Courier New',monospace;font-size:12px;}}
.tier-badge{{display:inline-block;padding:3px 8px;border-radius:999px;font-size:10px;font-weight:800;letter-spacing:0.04em;}}
.tier-free{{background:rgba(100,100,100,0.15);color:#888;}}
.tier-starter{{background:rgba(59,130,246,0.15);color:#60a5fa;}}
.tier-pro{{background:rgba(239,90,41,0.15);color:#ef5a29;}}
.alert{{background:rgba(239,90,41,0.1);border:1px solid rgba(239,90,41,0.25);border-radius:10px;padding:12px 16px;font-size:13px;color:#ef5a29;margin-bottom:20px;}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:16px;}}
.ts{{font-size:11px;color:#555;margin-top:2px;}}
@media(max-width:600px){{.grid2{{grid-template-columns:1fr;}}.stats-grid{{grid-template-columns:repeat(2,1fr);}}}}
</style>
</head>
<body>
<nav>
  <div class="brand">Sin<span>yal</span> <span style="font-size:12px;color:#555;font-weight:500;">Admin</span></div>
  <div class="nav-right">
    <span>&#128100; {ADMIN_USERNAME}</span>
    <form method="POST" action="/admin/logout" style="margin:0">
      <button class="btn-sm" type="submit">Logout</button>
    </form>
  </div>
</nav>
<main>
  {pending_alert}

  <div class="stats-grid">
    {stat_card('&#128100;', 'Total Users', total_users)}
    {stat_card('&#127774;', 'Baru Hari Ini', new_today, f'{new_week} minggu ini')}
    {stat_card('&#128279;', 'Total Affiliates', total_affiliates)}
    {stat_card('&#128101;', 'Total Referrals', total_referrals)}
    {stat_card('&#128190;', 'Payout Pending', pending_payouts, color='#fbbf24' if pending_payouts else '#ef5a29')}
    {stat_card('&#127925;', 'Playlists', total_playlists)}
    {stat_card('&#128278;', 'Saved Items', total_saved)}
  </div>

  <div class="grid2">
    <div class="section">
      <h2>&#127937; User per Tier</h2>
      <div class="card">
        <table>
          <thead><tr><th>Tier</th><th>Jumlah</th></tr></thead>
          <tbody>{tier_rows if tier_rows else '<tr><td colspan="2" style="color:#555">Belum ada data</td></tr>'}</tbody>
        </table>
      </div>
    </div>

    <div class="section">
      <h2>&#128196; Signup Terbaru</h2>
      <div class="card">
        <table>
          <thead><tr><th>Email</th><th>Tier</th><th>Tanggal</th></tr></thead>
          <tbody>{user_rows if user_rows else '<tr><td colspan="3" style="color:#555">Belum ada data</td></tr>'}</tbody>
        </table>
      </div>
    </div>
  </div>

  <div class="ts" style="margin-top:8px;">Data diambil live dari Supabase &mdash; refresh halaman untuk update.</div>
</main>
</body></html>
""")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
