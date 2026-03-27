#!/usr/bin/env python3
import asyncio
import base64
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

import httpx
from pathlib import Path

from fastapi import Body, FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse
from dotenv import load_dotenv

from scraper.tiktok import TikTokScraper
from scraper.youtube import YouTubeScraper
from scraper.instagram import InstagramScraper
from scraper.twitter import TwitterScraper
from scraper.facebook import FacebookScraper
from scraper.models import save_results

load_dotenv()

app = FastAPI(title="Sinyal - Content Intelligence")
SCRAPE_TIMEOUT_SECONDS = int(os.getenv("SCRAPE_TIMEOUT_SECONDS", "45"))
OUTPUT_DIR = Path("output").resolve()
PROFILE_CACHE_TTL_SECONDS = int(os.getenv("PROFILE_CACHE_TTL_SECONDS", "900"))
SEARCH_CACHE_TTL_SECONDS = int(os.getenv("SEARCH_CACHE_TTL_SECONDS", "900"))
COMMENTS_CACHE_TTL_SECONDS = int(os.getenv("COMMENTS_CACHE_TTL_SECONDS", "900"))
AUTH_COOKIE_NAME = "sinyal_access_token"
REFRESH_COOKIE_NAME = "sinyal_refresh_token"
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"
DEV_AUTH_BYPASS = os.getenv("DEV_AUTH_BYPASS", "false").lower() == "true"
PROFILE_CACHE: dict[tuple[str, int, str], tuple[float, dict]] = {}
SEARCH_CACHE: dict[tuple, tuple[float, dict]] = {}
COMMENTS_CACHE: dict[tuple[str, int], tuple[float, dict]] = {}
PLATFORM_SEARCH_CACHE: dict[tuple, tuple[float, list]] = {}
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
RATE_LIMIT_BUCKETS: dict[str, list[float]] = {}
RATE_LIMIT_RULES = {
    "auth_signup": (10, 300),
    "auth_signin": (15, 300),
    "search": (30, 300),
    "profile": (30, 300),
    "comments": (20, 300),
}


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
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
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
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
        "tagline": "Mulai riset konten viral tanpa biaya.",
        "limits": [
            "3 pencarian per hari",
            "TikTok saja",
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
    "starter": {
        "name": "Starter",
        "price_idr": 49_000,
        "tagline": "Riset konten harian tanpa batas pikiran.",
        "limits": [
            "30 pencarian per hari",
            "TikTok + Instagram",
            "20 cek profil per bulan",
            "20 tarik komentar per bulan",
            "10 transkrip video",
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
        "tagline": "Akses penuh ke semua platform dan fitur.",
        "limits": [
            "Pencarian unlimited",
            "Semua platform (TikTok, IG, YouTube, X, Facebook)",
            "Unlimited profil & komentar",
            "Unlimited transkrip",
            "Hook & CTA analysis",
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
    "lifetime": {
        "name": "Lifetime Deal",
        "price_idr": 299_000,
        "tagline": "Akses Pro selama 6 bulan. Terbatas 200 slot.",
        "limits": [
            "Semua fitur Pro",
            "Akses 6 bulan",
            "200 slot saja — habis tidak kembali",
        ],
        "cta": "Ambil Lifetime Deal",
        "env_key": "MAYAR_URL_LIFETIME",
        "accent": "forest",
        "daily_search_limit": 0,
        "monthly_search_limit": 0,
        "monthly_profile_limit": 0,
        "monthly_comment_limit": 0,
        "monthly_transcript_limit": 0,
        "allowed_platforms": ["tiktok", "instagram", "youtube", "twitter", "facebook"],
        "watermark_exports": False,
        "billing_interval": "lifetime",
    },
}

LIFETIME_SLOTS_TOTAL = int(os.getenv("LIFETIME_SLOTS_TOTAL", "200"))
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


async def get_authenticated_user(request: Request) -> dict | None:
    if not supabase_auth_configured():
        return None

    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token:
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
    eyebrow: str,
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
):
    aside_items = "".join(f"<li>{item}</li>" for item in aside_list)
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
  --card: rgba(255,250,244,0.9);
  --accent: #c0391b;
  --accent-2: #ef5a29;
  --orange: #ef5a29;
  --orange-2: #ff8d42;
  --green: #285f58;
}}
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:'Plus Jakarta Sans',sans-serif;color:var(--ink);background:var(--bg);-webkit-font-smoothing:antialiased;min-height:100vh;}}
a{{text-decoration:none;color:inherit;}}

/* Nav */
.topbar{{position:sticky;top:0;z-index:50;background:rgba(250,243,236,0.85);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);border-bottom:1px solid var(--line);}}
.topbar-inner{{display:flex;align-items:center;justify-content:space-between;height:64px;width:min(1120px,100% - 40px);margin:0 auto;}}
.brand{{font-family:'DM Serif Display',serif;font-size:24px;letter-spacing:-0.03em;color:var(--accent);font-weight:400;}}
.topnav{{display:flex;align-items:center;gap:8px;}}
.topnav a{{font-size:14px;font-weight:600;color:var(--soft);padding:8px 14px;border-radius:10px;transition:all .15s;}}
.topnav a:hover{{background:rgba(192,57,27,0.06);color:var(--accent);}}
.btn{{display:inline-flex;align-items:center;justify-content:center;gap:8px;padding:12px 22px;border-radius:12px;font-weight:800;font-size:14px;border:none;cursor:pointer;transition:transform .15s,box-shadow .15s;}}
.btn:hover{{transform:translateY(-1px);}}
.btn-primary{{background:linear-gradient(135deg,var(--accent),var(--accent-2));color:#fff;box-shadow:0 8px 24px rgba(192,57,27,0.22);}}

/* Layout */
.page-wrap{{width:min(960px,100% - 40px);margin:0 auto;padding:48px 0 80px;}}
.layout{{display:grid;grid-template-columns:1.1fr 0.9fr;gap:20px;align-items:start;}}
.panel{{background:var(--card);border:1px solid var(--line);border-radius:20px;box-shadow:0 12px 40px rgba(98,66,43,0.06);}}

/* Left panel */
.info-panel{{padding:32px;}}
.eyebrow{{display:inline-flex;align-items:center;gap:8px;padding:8px 14px;border-radius:999px;background:rgba(192,57,27,0.06);border:1px solid rgba(192,57,27,0.08);color:var(--accent);font-size:12px;font-weight:800;letter-spacing:0.04em;text-transform:uppercase;margin-bottom:16px;}}
.eyebrow::before{{content:"";width:7px;height:7px;border-radius:50%;background:var(--accent);}}
h1{{font-family:'DM Serif Display',serif;font-size:clamp(28px,4vw,42px);line-height:1.08;letter-spacing:-0.04em;margin-bottom:12px;}}
.lead{{color:var(--soft);font-size:15px;line-height:1.7;}}
.info-grid{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:20px;}}
.info-card{{padding:14px;border-radius:14px;background:rgba(255,255,255,0.6);border:1px solid rgba(84,52,29,0.04);}}
.info-card strong{{display:block;font-size:14px;margin-bottom:3px;}}
.info-card span{{color:var(--muted);font-size:12px;line-height:1.5;}}
.aside-section{{margin-top:20px;padding:20px;border-radius:16px;background:rgba(255,255,255,0.5);border:1px solid var(--line);}}
.aside-section h3{{font-size:18px;font-weight:800;margin-bottom:8px;}}
.aside-section p{{color:var(--soft);font-size:14px;line-height:1.7;margin-bottom:12px;}}
.aside-section ul{{list-style:none;display:grid;gap:8px;}}
.aside-section li{{padding:10px 14px;border-radius:12px;background:rgba(255,255,255,0.6);border:1px solid rgba(84,52,29,0.04);color:var(--soft);font-size:13px;line-height:1.6;}}

/* Form panel */
.form-panel{{padding:28px;display:flex;flex-direction:column;gap:14px;}}
.form-panel h2{{font-family:'DM Serif Display',serif;font-size:24px;letter-spacing:-0.02em;}}
.form-panel p{{color:var(--soft);font-size:14px;line-height:1.6;}}
.field{{display:grid;gap:5px;}}
.field label{{font-size:12px;font-weight:800;color:var(--muted);text-transform:uppercase;letter-spacing:0.04em;}}
.field input,.field select{{width:100%;padding:12px 14px;border-radius:12px;border:1px solid var(--line);background:rgba(255,255,255,0.7);font:inherit;font-size:14px;color:var(--ink);outline:none;transition:border-color .15s;}}
.field input:focus,.field select:focus{{border-color:var(--accent);}}
.submit{{border:0;cursor:pointer;padding:14px 18px;border-radius:12px;background:linear-gradient(135deg,var(--accent),var(--accent-2));color:#fff;font:inherit;font-weight:800;font-size:15px;box-shadow:0 8px 24px rgba(192,57,27,0.2);transition:transform .15s,box-shadow .15s;}}
.submit:hover{{transform:translateY(-1px);box-shadow:0 12px 32px rgba(192,57,27,0.3);}}
.sub-actions{{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;align-items:center;}}
.sub-actions a{{color:var(--soft);font-size:13px;font-weight:700;}}
.sub-actions a:hover{{color:var(--accent);}}
.note{{padding:12px 14px;border-radius:12px;background:rgba(40,95,88,0.08);color:var(--green);font-size:13px;line-height:1.6;}}

@media(max-width:768px){{
  .layout{{grid-template-columns:1fr;}}
  .info-grid{{grid-template-columns:1fr;}}
}}
</style>
</head>
<body>
<div class="topbar">
  <div class="topbar-inner">
    <a class="brand" href="/">Sinyal</a>
    <div class="topnav">
      <a href="/signin">Masuk</a>
      <a href="/signup">Daftar</a>
      <a href="/payment">Harga</a>
      <a href="/app" class="btn btn-primary" style="padding:10px 18px;font-size:13px;">Buka App</a>
    </div>
  </div>
</div>
<div class="page-wrap">
  <div class="layout">
    <div class="panel info-panel">
      <div class="eyebrow">{eyebrow}</div>
      <h1>{heading}</h1>
      <p class="lead">{subheading}</p>
      <div class="aside-section">
        <h3>{aside_title}</h3>
        <p>{aside_body}</p>
        <ul>{aside_items}</ul>
      </div>
    </div>
    <div class="panel form-panel">
      <h2>{primary_label}</h2>
      {form_fields}
      <button class="submit" type="button">{primary_label}</button>
      <div class="sub-actions">
        <a href="{secondary_href}">{secondary_label}</a>
        <a href="/payment">Lihat paket</a>
      </div>
    </div>
  </div>
</div>
{extra_script}
</body>
</html>"""


def render_payment_page():
    plan_cards = []
    for plan in get_plan_catalog():
        featured = " featured" if plan["code"] == "pro" else ""
        badge_text = "Paling populer" if plan["code"] == "pro" else ("Gratis selamanya" if plan["code"] == "free" else ("Terbatas 200 slot" if plan["code"] == "lifetime" else "Mulai serius"))
        button_href = f"/checkout/{plan['code']}"
        button_label = plan["cta"]
        limits_html = "".join(f"<div>{item}</div>" for item in plan["limits"])
        plan_cards.append(
            f"""
            <div class="price-card{featured}">
              <div class="badge">{badge_text}</div>
              <h3>{plan['name']}</h3>
              <div class="price">{plan['price_label']} <small>/ bulan</small></div>
              <div class="price-note">{plan['tagline']}</div>
              <div class="price-list">{limits_html}</div>
              <div class="price-cta"><a href="{button_href}">{button_label}</a></div>
            </div>
            """
        )

    plans_html = "".join(plan_cards)
    return f"""<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Harga - Sinyal</title>
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
  --card: rgba(255,250,244,0.9);
  --accent: #c0391b;
  --accent-2: #ef5a29;
  --orange: #ef5a29;
  --orange-2: #ff8d42;
  --green: #285f58;
}}
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:'Plus Jakarta Sans',sans-serif;color:var(--ink);background:var(--bg);-webkit-font-smoothing:antialiased;}}
a{{text-decoration:none;color:inherit;}}
.wrap{{width:min(1120px,100% - 40px);margin:0 auto;}}

/* Nav */
nav{{position:sticky;top:0;z-index:50;background:rgba(250,243,236,0.85);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);border-bottom:1px solid var(--line);}}
.nav-inner{{display:flex;align-items:center;justify-content:space-between;height:64px;}}
.brand{{font-family:'DM Serif Display',serif;font-size:24px;letter-spacing:-0.03em;color:var(--accent);font-weight:400;}}
.nav-links{{display:flex;align-items:center;gap:8px;}}
.nav-links a{{font-size:14px;font-weight:600;color:var(--soft);padding:8px 14px;border-radius:10px;transition:all .15s;}}
.nav-links a:hover{{background:rgba(192,57,27,0.06);color:var(--accent);}}
.btn{{display:inline-flex;align-items:center;justify-content:center;gap:8px;padding:12px 22px;border-radius:12px;font-weight:800;font-size:14px;border:none;cursor:pointer;transition:transform .15s,box-shadow .15s;}}
.btn:hover{{transform:translateY(-1px);}}
.btn-primary{{background:linear-gradient(135deg,var(--accent),var(--accent-2));color:#fff;box-shadow:0 8px 24px rgba(192,57,27,0.22);}}

/* Page header */
.page-header{{text-align:center;padding:56px 0 40px;}}
.page-header h1{{font-family:'DM Serif Display',serif;font-size:clamp(32px,5vw,52px);line-height:1.05;letter-spacing:-0.04em;margin-bottom:12px;}}
.page-header p{{color:var(--soft);font-size:16px;line-height:1.7;max-width:500px;margin:0 auto;}}

/* Pricing */
.pricing-wrap{{padding:32px;border-radius:24px;background:var(--card);border:1px solid var(--line);box-shadow:0 12px 40px rgba(98,66,43,0.06);margin-bottom:24px;}}
.pricing-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;}}
.price-card{{padding:24px;border-radius:20px;background:rgba(255,255,255,0.8);border:1px solid var(--line);display:flex;flex-direction:column;transition:transform .2s,box-shadow .2s;}}
.price-card:hover{{transform:translateY(-3px);box-shadow:0 16px 40px rgba(98,66,43,0.08);}}
.price-card.featured{{background:linear-gradient(180deg,rgba(192,57,27,0.08),rgba(255,255,255,0.96));border-color:rgba(192,57,27,0.15);}}
.badge{{display:inline-flex;align-self:flex-start;padding:6px 12px;border-radius:999px;background:rgba(192,57,27,0.06);color:var(--accent);font-size:11px;font-weight:800;letter-spacing:0.02em;margin-bottom:14px;}}
.price-card h3{{font-size:22px;font-weight:800;margin-bottom:8px;}}
.price{{font-family:'DM Serif Display',serif;font-size:40px;letter-spacing:-0.04em;line-height:1;}}
.price small{{font-family:'Plus Jakarta Sans',sans-serif;font-size:14px;color:var(--muted);font-weight:600;}}
.price-note{{margin:10px 0 16px;color:var(--soft);font-size:13px;line-height:1.65;}}
.price-list{{display:grid;gap:8px;color:var(--soft);font-size:13px;line-height:1.6;flex:1;}}
.price-list div{{display:flex;align-items:center;gap:8px;}}
.price-list div::before{{content:"\2713";color:var(--accent);font-weight:800;flex-shrink:0;font-size:14px;}}
.price-cta{{display:flex;align-items:center;justify-content:center;width:100%;margin-top:auto;padding-top:20px;}}
.price-cta a{{display:flex;align-items:center;justify-content:center;width:100%;padding:13px;border-radius:14px;color:#fff;font-weight:800;font-size:14px;background:linear-gradient(135deg,var(--accent),var(--accent-2));box-shadow:0 8px 24px rgba(192,57,27,0.18);transition:transform .15s,box-shadow .15s;}}
.price-cta a:hover{{transform:translateY(-1px);box-shadow:0 12px 32px rgba(192,57,27,0.28);}}

/* Footer note */
.footnote{{padding:16px 20px;border-radius:16px;background:rgba(40,95,88,0.06);color:var(--green);font-size:14px;line-height:1.7;margin-bottom:40px;text-align:center;}}
.footnote a{{color:var(--accent);font-weight:800;}}

@media(max-width:1100px){{.pricing-grid{{grid-template-columns:repeat(2,1fr);}}}}
@media(max-width:720px){{.pricing-grid{{grid-template-columns:1fr;}}}}
</style>
</head>
<body>
<nav>
  <div class="wrap nav-inner">
    <a href="/" class="brand">Sinyal</a>
    <div class="nav-links">
      <a href="/signin">Masuk</a>
      <a href="/signup">Daftar</a>
      <a href="/app" class="btn btn-primary" style="padding:10px 18px;font-size:13px;">Buka App</a>
    </div>
  </div>
</nav>
<div class="wrap">
  <div class="page-header">
    <h1>Harga yang masih masuk akal</h1>
    <p>Mulai gratis, upgrade kalau butuh lebih.</p>
  </div>
  <div class="pricing-wrap">
    <div class="pricing-grid">
      {plans_html}
    </div>
  </div>
  <div class="footnote">
    Punya pertanyaan soal paket? <a href="/signin">Masuk</a> dulu atau <a href="/">kembali ke beranda</a>.
  </div>
</div>
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
        eyebrow="Buka akun baru",
        heading="Bikin akun, pilih paket, lalu langsung mulai riset.",
        subheading="Buka akun dulu, lalu lanjut pilih paket dan masuk ke workspace riset tanpa ribet.",
        primary_label="Daftar Sekarang",
        secondary_label="Sudah punya akun? Masuk di sini",
        secondary_href="/signin",
        form_fields="""
        <div class="field"><label>Nama lengkap</label><input id="signupFullName" type="text" placeholder="Nama kamu atau nama tim" required></div>
        <div class="field"><label>Nama usaha / tim</label><input id="signupCompanyName" type="text" placeholder="Nama brand atau agency" required></div>
        <div class="field"><label>Email kerja</label><input id="signupEmail" type="email" placeholder="nama@brand.com" required></div>
        <div class="field"><label>Password</label><input id="signupPassword" type="password" placeholder="Minimal 8 karakter" minlength="8" required></div>
        <div class="field"><label>Kamu pakai untuk apa?</label><select id="signupUseCase"><option>Riset konten</option><option>Vetting creator</option><option>Agency / tim sosial media</option><option>UMKM / brand</option></select></div>
        <div id="signupStatus" class="note">Isi data di bawah, lalu lanjut ke langkah berikutnya.</div>
        """,
        aside_title="Apa yang terjadi setelah daftar",
        aside_body="Begitu akun siap, kamu bisa pilih paket dan langsung masuk ke workspace riset tanpa setup manual yang bikin capek.",
        aside_list=[
            "Akun siap dipakai untuk menyimpan workflow dan hasil riset.",
            "Akses fitur menyesuaikan paket yang kamu pilih.",
            "Begitu aktif, kamu bisa langsung mulai cari pola konten yang lagi jalan.",
        ],
        footer_note="Fokus halaman ini sederhana: daftar cepat, lanjut, lalu mulai kerja.", 
        extra_script="""
        <script>
        document.querySelector('.submit')?.addEventListener('click', async () => {
          const status = document.getElementById('signupStatus');
          const fullName = document.getElementById('signupFullName').value.trim();
          const companyName = document.getElementById('signupCompanyName').value.trim();
          const email = document.getElementById('signupEmail').value.trim();
          const password = document.getElementById('signupPassword').value;
          const useCase = document.getElementById('signupUseCase').value;
          // Frontend validation
          if (!fullName) { status.textContent = 'Nama lengkap wajib diisi.'; return; }
          if (!companyName) { status.textContent = 'Nama usaha / tim wajib diisi.'; return; }
          if (!email || !email.includes('@')) { status.textContent = 'Email tidak valid.'; return; }
          if (password.length < 8) { status.textContent = 'Password minimal 8 karakter.'; return; }
          status.textContent = 'Lagi bikin akun...';
          const payload = { full_name: fullName, company_name: companyName, email, password, onboarding_use_case: useCase };
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
          status.textContent = 'Akun berhasil dibuat. Lagi cek langkah berikutnya...';
          window.location.href = '/start';
        });
        </script>
        """,
    )


@app.get("/signin", response_class=HTMLResponse)
async def signin_page():
    return render_public_account_page(
        title="Masuk Sinyal",
        eyebrow="Masuk ke akun kamu",
        heading="Masuk cepat, lalu lanjut ke workspace risetmu.",
        subheading="Masuk cepat, lalu lanjut langsung ke workspace risetmu.",
        primary_label="Masuk",
        secondary_label="Belum punya akun? Daftar sekarang",
        secondary_href="/signup",
        form_fields="""
        <div class="field"><label>Email</label><input id="signinEmail" type="email" placeholder="nama@brand.com"></div>
        <div class="field"><label>Password</label><input id="signinPassword" type="password" placeholder="Masukkan password"></div>
        <div class="field"><label>Mode kerja</label><select><option>Ingat saya di perangkat ini</option><option>Perangkat tim bersama</option></select></div>
        <div id="signinStatus" class="note">Masuk dulu untuk lanjut ke app.</div>
        """,
        aside_title="Setelah masuk",
        aside_body="Begitu login berhasil, kamu bisa lanjut ke app dan mulai riset tanpa pindah-pindah tempat.",
        aside_list=[
            "Masuk ke workspace riset lebih cepat.",
            "Lanjut ke paket kalau akses belum aktif.",
            "Kembali kerja tanpa bingung cari halaman yang benar.",
        ],
        footer_note="Fokus signin ini sederhana: masuk cepat dan lanjut kerja.", 
        extra_script="""
        <script>
        document.querySelector('.submit')?.addEventListener('click', async () => {
          const status = document.getElementById('signinStatus');
          const email = document.getElementById('signinEmail').value.trim();
          const password = document.getElementById('signinPassword').value;
          // Frontend validation
          if (!email || !email.includes('@')) { status.textContent = 'Email tidak valid.'; return; }
          if (!password) { status.textContent = 'Password wajib diisi.'; return; }
          status.textContent = 'Lagi masuk...';
          const payload = {
            email,
            password,
          };
          const res = await fetch('/api/auth/signin', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
          });
          const data = await res.json();
          if (!res.ok) {
            status.textContent = data.msg || data.error_description || data.error || 'Gagal masuk.';
            return;
          }
          status.textContent = 'Login berhasil. Lagi cek langkah berikutnya...';
          window.location.href = '/start';
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
    return response


@app.post("/api/auth/signin")
async def auth_signin(request: Request, payload: dict = Body(...)):
    rate_limited = enforce_rate_limit(request, "auth_signin")
    if rate_limited:
        return rate_limited
    email = normalize_text(payload.get("email"))
    password = payload.get("password", "")

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
        return {"received": True, "provider": "mayar", "status": "upgraded", "email": payer_email, "new_tier": tier}

    return {"received": True, "provider": "mayar", "status": "profile_not_found"}

@app.get("/app", response_class=HTMLResponse)
async def app_page(request: Request):
    # Allow unauthenticated access for FREE tier
    return APP_HTML


@app.get("/account", response_class=HTMLResponse)
async def account_page(request: Request):
    return ACCOUNT_HTML


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

    tasks = [
        scrape_platform(p, kw)
        for kw in keywords
        for p in platform_list
    ]
    results_list = await asyncio.gather(*tasks, return_exceptions=True)

    for result in results_list:
        if isinstance(result, Exception):
            print(f"Error: {result}")
        elif result:
            all_results.extend(result)

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
      <div class="pricing-header"><h2>Harga yang masih masuk akal</h2><p>Mulai gratis, upgrade kalau butuh lebih.</p></div>
      <div class="pricing-grid">
        <div class="price-card">
          <div class="badge">Gratis selamanya</div><h3>Free</h3><div class="price">Rp0 <small>/ selamanya</small></div><div class="price-note">Mulai riset konten viral tanpa biaya.</div>
          <div class="price-list"><div>3 pencarian per hari</div><div>TikTok saja</div><div>Watermark di export</div></div>
          <div class="price-cta"><a href="/signup">Mulai Gratis</a></div>
        </div>
        <div class="price-card">
          <div class="badge">Mulai serius</div><h3>Starter</h3><div class="price">Rp49rb <small>/ bulan</small></div><div class="price-note">Riset konten harian tanpa batas pikiran.</div>
          <div class="price-list"><div>30 pencarian per hari</div><div>TikTok + Instagram</div><div>20 cek profil per bulan</div><div>20 tarik komentar per bulan</div><div>10 transkrip video</div></div>
          <div class="price-cta"><a href="/checkout/starter">Ambil Starter</a></div>
        </div>
        <div class="price-card featured">
          <div class="badge">Paling populer</div><h3>Pro</h3><div class="price">Rp99rb <small>/ bulan</small></div><div class="price-note">Akses penuh ke semua platform dan fitur.</div>
          <div class="price-list"><div>Pencarian unlimited</div><div>Semua platform (TikTok, IG, YT, X, FB)</div><div>Unlimited profil &amp; komentar</div><div>Unlimited transkrip</div><div>Hook &amp; CTA analysis</div></div>
          <div class="price-cta"><a href="/checkout/pro">Upgrade ke Pro</a></div>
        </div>
        <div class="price-card">
          <div class="badge">Terbatas 200 slot</div><h3>Lifetime Deal</h3><div class="price">Rp299rb <small>/ 6bulan</small></div><div class="price-note">Akses Pro selama 6 bulan. Habis tidak kembali.</div>
          <div class="price-list"><div>Semua fitur Pro</div><div>Akses 6 bulan penuh</div><div>200 slot saja</div></div>
          <div class="price-cta"><a href="/checkout/lifetime">Ambil Lifetime Deal</a></div>
        </div>
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
    <div class="footer-links"><a href="/signup">Daftar</a><a href="/signin">Masuk</a><a href="/payment">Pricing</a><a href="#fitur">Product</a></div>
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
    document.getElementById('planPrice').textContent = fmtPrice(plan.price_idr) + (plan.billing_interval === 'monthly' ? '/bulan' : plan.billing_interval === 'lifetime' ? '/6 bulan' : '');
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
    if (tier === 'pro' || tier === 'lifetime') {
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

/* ── Result cards ── */
.result-card{background:var(--card);border:1.5px solid var(--line);border-radius:var(--radius);padding:22px;margin-bottom:12px;transition:all .2s ease;}
.result-card:hover{transform:translateY(-2px);box-shadow:var(--shadow-lg);border-color:rgba(239,90,41,0.12);}
.result-meta{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:10px;}
.result-badge{padding:5px 12px;border-radius:999px;background:linear-gradient(135deg,rgba(239,90,41,0.1),rgba(255,141,66,0.06));color:var(--accent);font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:0.04em;}
.result-author{font-size:12px;color:var(--muted);font-weight:600;}
.result-title{font-size:15px;font-weight:800;line-height:1.45;margin-bottom:8px;display:block;color:var(--ink);transition:color .15s;}
.result-title:hover{color:var(--accent);}
.result-caption{font-size:13px;color:var(--soft);line-height:1.65;margin-bottom:12px;}
.result-transcript{margin-bottom:12px;padding:14px 16px;border-radius:14px;background:rgba(255,255,255,0.6);border-left:3px solid rgba(239,90,41,0.25);font-size:12px;color:var(--soft);font-style:italic;line-height:1.7;}
.result-stats{display:flex;gap:16px;flex-wrap:wrap;align-items:center;}
.result-stat{display:flex;align-items:center;gap:5px;font-size:12px;color:var(--muted);}
.result-stat strong{color:var(--ink);font-weight:800;}
.result-stat svg{width:14px;height:14px;opacity:0.6;}
.result-tags{display:flex;gap:4px;flex-wrap:wrap;margin-left:auto;}
.result-tag{padding:3px 10px;border-radius:8px;background:rgba(84,52,29,0.04);font-size:10px;color:var(--muted);font-weight:700;}
.copy-btn{display:inline-flex;align-items:center;gap:5px;padding:7px 12px;border-radius:10px;background:rgba(239,90,41,0.08);color:var(--accent);font-size:11px;font-weight:800;border:none;cursor:pointer;transition:all .15s;flex-shrink:0;}
.copy-btn:hover{background:rgba(239,90,41,0.16);transform:scale(1.03);}

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
          <input id="globalSearch" placeholder="Quick find..." type="text"/>
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
        </div>

        <!-- Platforms -->
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
            <div class="step-item"><div class="step-num">3</div><strong>Download</strong><span>Export JSON / CSV langsung</span></div>
          </div>
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
            <input id="minViews" type="number" class="ds-input" placeholder="Min views"/>
          </div>
          <div class="filter-row" style="margin-top:8px;">
            <input id="maxViews" type="number" class="ds-input" placeholder="Max views"/>
            <input id="minLikes" type="number" class="ds-input" placeholder="Min likes"/>
            <input id="maxLikes" type="number" class="ds-input" placeholder="Max likes"/>
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

    </div>
  </div>
</div>

<script>
window.onerror = function(msg, src, line, col, err) {
  console.error('[SINYAL ERROR]', msg, 'at', src, line, col);
};
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
  var valid = ['dashboard','search','profile','comments'];
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
function rowResult(item) {
  const rid = _rowId++;
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

  return '<div class="result-card">' +
    '<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px;margin-bottom:8px;">' +
      '<div style="flex:1;min-width:0;">' +
        '<div class="result-meta">' +
          '<span class="result-badge">' + escapeHTML(item.platform || 'N/A') + '</span>' +
          (author ? (authorUrl ? '<a href="' + authorUrl + '" target="_blank" class="result-author" style="color:var(--muted);">@' + author + '</a>' : '<span class="result-author">@' + author + '</span>') : '') +
          (music ? '<span class="result-author" style="display:flex;align-items:center;gap:3px;">\u266B ' + music + '</span>' : '') +
        '</div>' +
        '<a href="' + videoUrl + '" target="_blank" class="result-title">' + hook + '</a>' +
      '</div>' +
      '<button onclick="copyHook(this.dataset.hook)" data-hook="' + rawHook + '" class="copy-btn">Copy</button>' +
    '</div>' +
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
  document.getElementById('searchMeta').innerHTML = '<span style="display:inline-flex;align-items:center;gap:6px;"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="animation:spin 1s linear infinite;"><path d="M21 12a9 9 0 11-6.219-8.56"/></svg> Menjalankan scan...</span>';
  var jsonLink = document.getElementById('jsonDownload');
  var csvLink = document.getElementById('csvDownload');
  var pdfLink = document.getElementById('pdfDownload');
  jsonLink.style.display = 'none';
  csvLink.style.display = 'none';
  pdfLink.style.display = 'none';
  try {
    var res = await fetch('/api/search?' + params.toString());
    var data = await res.json();
    if (!res.ok) {
      if (res.status === 429 || res.status === 402 || res.status === 400) {
        document.getElementById('searchMeta').innerHTML = '<span style="color:var(--accent);font-weight:800;">TERTOLAK: ' + escapeHTML(data.error) + '</span> <a href="' + (data.upgrade_url || '/payment') + '" style="margin-left:8px;display:inline-block;padding:8px 16px;border-radius:10px;background:linear-gradient(135deg,var(--accent),var(--accent-2));color:#fff;font-size:12px;font-weight:800;">Upgrade Sekarang</a>';
        document.getElementById('searchResults').innerHTML = '';
        return;
      }
      throw new Error(data.error || 'Server error');
    }
    document.getElementById('searchMeta').innerHTML = '<span style="display:inline-flex;align-items:center;gap:6px;color:var(--green);"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg> ' + (data.results ? data.results.length : 0) + ' hasil ditemukan</span>';
    document.getElementById('searchResults').innerHTML = (data.results || []).map(rowResult).join('') || '<div class="empty-state" style="padding:32px;"><p>Tidak ada hasil untuk keyword ini.</p></div>';
    if(data.json_file){ jsonLink.href = '/api/download?file=' + encodeURIComponent(data.json_file); jsonLink.style.display = 'inline-flex'; }
    if(data.csv_file){ csvLink.href = '/api/download?file=' + encodeURIComponent(data.csv_file); csvLink.style.display = 'inline-flex'; }
    if(data.pdf_file){ pdfLink.href = '/api/download?file=' + encodeURIComponent(data.pdf_file); pdfLink.style.display = 'inline-flex'; }
  } catch (err) {
    document.getElementById('searchMeta').innerHTML = '<span style="color:var(--accent);">ERROR: ' + escapeHTML(err.message) + '</span>';
  }
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
