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
SCRAPE_TIMEOUT_SECONDS = int(os.getenv("SCRAPE_TIMEOUT_SECONDS", "120"))
OUTPUT_DIR = Path("output").resolve()
PROFILE_CACHE_TTL_SECONDS = int(os.getenv("PROFILE_CACHE_TTL_SECONDS", "900"))
SEARCH_CACHE_TTL_SECONDS = int(os.getenv("SEARCH_CACHE_TTL_SECONDS", "900"))
COMMENTS_CACHE_TTL_SECONDS = int(os.getenv("COMMENTS_CACHE_TTL_SECONDS", "900"))
AUTH_COOKIE_NAME = "sinyal_access_token"
REFRESH_COOKIE_NAME = "sinyal_refresh_token"
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"
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
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.tailwindcss.com; "
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
            "Semua platform (TikTok, IG, X, Facebook)",
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
    except Exception:
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


def infer_plan_code(product_name: str, amount: int) -> str | None:
    normalized_name = normalize_text(product_name).lower()
    for code, plan in PLAN_CATALOG.items():
        if normalized_name and (
            code in normalized_name
            or normalize_text(plan["name"]).lower() in normalized_name
        ):
            return code
    for code, plan in PLAN_CATALOG.items():
        if amount == plan["price_idr"]:
            return code
    return None


def billing_period_end(start_at: datetime | None, plan_code: str | None) -> datetime | None:
    if not start_at or not plan_code:
        return None
    billing_interval = "monthly"
    if plan_code in PLAN_CATALOG:
        billing_interval = "yearly" if PLAN_CATALOG[plan_code].get("billing_interval") == "yearly" else "monthly"
    if billing_interval == "yearly":
        return start_at + timedelta(days=365)
    return start_at + timedelta(days=30)


async def fetch_profile_by_email(email: str) -> dict | None:
    status_code, data, _ = await supabase_rest_request(
        "GET",
        "/rest/v1/profiles",
        params={
            "select": "user_id,email,full_name,company_name,phone,role,onboarding_use_case",
            "email": f"eq.{email}",
            "limit": "1",
        },
    )
    if status_code != 200 or not isinstance(data, list) or not data:
        return None
    return data[0]


async def fetch_plan_row(plan_code: str) -> dict | None:
    status_code, data, _ = await supabase_rest_request(
        "GET",
        "/rest/v1/plans",
        params={
            "select": "*",
            "code": f"eq.{plan_code}",
            "limit": "1",
        },
    )
    if status_code != 200 or not isinstance(data, list) or not data:
        return None
    return data[0]


async def fetch_latest_subscription(user_id: str) -> dict | None:
    status_code, data, _ = await supabase_rest_request(
        "GET",
        "/rest/v1/subscriptions",
        params={
            "select": "*",
            "user_id": f"eq.{user_id}",
            "order": "created_at.desc",
            "limit": "1",
        },
    )
    if status_code != 200 or not isinstance(data, list) or not data:
        return None
    return data[0]


async def fetch_current_subscription(user_id: str) -> tuple[dict | None, dict | None]:
    subscription = await fetch_latest_subscription(user_id)
    if not subscription:
        return None, None
    plan = await fetch_plan_row(subscription.get("plan_code"))
    return subscription, plan


def subscription_is_active(subscription: dict | None) -> bool:
    if not subscription:
        return False
    if subscription.get("status") != "active":
        return False
    current_period_end = parse_upload_date(subscription.get("current_period_end"))
    if current_period_end and current_period_end < datetime.now(timezone.utc):
        return False
    return True


def usage_period_start(subscription: dict | None) -> datetime:
    if subscription:
        start = parse_upload_date(subscription.get("current_period_start"))
        if start:
            return start
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def get_usage_total(user_id: str, event_type: str, since: datetime) -> int:
    status_code, data, _ = await supabase_rest_request(
        "GET",
        "/rest/v1/usage_events",
        params={
            "select": "units",
            "user_id": f"eq.{user_id}",
            "event_type": f"eq.{event_type}",
            "created_at": f"gte.{since.isoformat()}",
            "limit": "1000",
        },
    )
    if status_code != 200 or not isinstance(data, list):
        return 0
    return sum(int(item.get("units") or 0) for item in data)


async def record_usage_event(user_id: str, event_type: str, metadata: dict | None = None, units: int = 1):
    await supabase_rest_request(
        "POST",
        "/rest/v1/usage_events",
        payload={
            "user_id": user_id,
            "event_type": event_type,
            "units": units,
            "metadata": metadata or {},
        },
        prefer="return=minimal",
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
    # If auth is not configured, allow everything (dev mode)
    if not supabase_auth_configured():
        return None, None, None

    user = await get_authenticated_user(request)

    # --- FREE TIER: unauthenticated users ---
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
        # Non-search features require auth
        return None, None, JSONResponse(
            {"error": "Silakan login dulu untuk memakai fitur ini.", "code": "auth_required"},
            status_code=401,
        )

    # --- AUTHENTICATED USERS ---
    if not supabase_rest_configured():
        return user, None, None

    subscription, plan = await fetch_current_subscription(user["id"])

    # No active subscription → treat as FREE tier
    if not subscription_is_active(subscription) or not plan:
        plan = _get_free_plan()

    # --- Daily search limit check ---
    if feature == "search":
        daily_limit = int(plan.get("daily_search_limit") or 0)
        if daily_limit > 0:
            used, _ = _check_ip_daily_search(request)
            if used >= daily_limit:
                return user, plan, JSONResponse(
                    {
                        "error": f"Kuota pencarian hari ini habis ({daily_limit}/hari). Upgrade untuk lebih banyak.",
                        "code": "quota_exceeded",
                        "feature": feature,
                        "limit": daily_limit,
                        "used": used,
                        "plan_code": plan.get("code"),
                        "upgrade_url": "/payment",
                    },
                    status_code=429,
                )

    # --- Monthly limit check for profile/comments/transcript ---
    limit_map = {
        "profile": "monthly_profile_limit",
        "comments": "monthly_comment_limit",
        "transcript": "monthly_transcript_limit",
    }
    limit_field = limit_map.get(feature)
    if limit_field:
        limit_value = int(plan.get(limit_field) or 0)
        if limit_value > 0:
            used = await get_usage_total(user["id"], feature, usage_period_start(subscription))
            if used >= limit_value:
                return user, plan, JSONResponse(
                    {
                        "error": "Kuota paket bulan ini sudah habis.",
                        "code": "quota_exceeded",
                        "feature": feature,
                        "limit": limit_value,
                        "used": used,
                        "plan_code": plan.get("code"),
                        "upgrade_url": "/payment",
                    },
                    status_code=429,
                )
        elif limit_value == 0 and plan.get("code") == "free":
            return user, plan, JSONResponse(
                {
                    "error": "Fitur ini belum tersedia di paket Free. Upgrade untuk akses.",
                    "code": "feature_locked",
                    "feature": feature,
                    "plan_code": "free",
                    "upgrade_url": "/payment",
                },
                status_code=402,
            )

    return user, plan, None


def mayar_secret_matches(request: Request) -> bool:
    expected = os.getenv("MAYAR_WEBHOOK_SECRET", "").strip()
    if not expected:
        return True
    candidates = [
        request.headers.get("x-webhook-secret", ""),
        request.headers.get("x-mayar-webhook-secret", ""),
    ]
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        candidates.append(auth_header.split(" ", 1)[1].strip())
    return any(candidate == expected for candidate in candidates)


async def upsert_payment_transaction(payment: dict) -> dict | None:
    provider_invoice_id = payment.get("provider_invoice_id")
    existing = None
    if provider_invoice_id:
        status_code, data, _ = await supabase_rest_request(
            "GET",
            "/rest/v1/payment_transactions",
            params={
                "select": "*",
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
    aside_title: str,
    aside_body: str,
    aside_list: list[str],
    footer_note: str,
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
  --bg: #f7efe4;
  --ink: #1f1711;
  --soft: #725a4b;
  --muted: #9b8576;
  --line: rgba(80, 52, 31, 0.12);
  --card: rgba(255,255,255,0.84);
  --accent: #ef5a29;
  --accent-2: #ff8d42;
  --accent-soft: rgba(239,90,41,0.1);
  --green: #295d57;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  font-family: 'Plus Jakarta Sans', sans-serif;
  color: var(--ink);
  min-height: 100vh;
  background:
    radial-gradient(circle at top left, rgba(239,90,41,0.16), transparent 28%),
    radial-gradient(circle at top right, rgba(41,93,87,0.14), transparent 24%),
    linear-gradient(180deg, #fffaf4 0%, #f7efe4 56%, #f0e2d2 100%);
}}
body::before {{
  content: "";
  position: fixed;
  inset: 0;
  pointer-events: none;
  background-image:
    linear-gradient(rgba(80,52,31,0.03) 1px, transparent 1px),
    linear-gradient(90deg, rgba(80,52,31,0.03) 1px, transparent 1px);
  background-size: 34px 34px;
  mask-image: linear-gradient(180deg, rgba(0,0,0,0.65), transparent 90%);
}}
.page {{ position: relative; z-index: 1; padding: 28px 16px 36px; }}
.shell {{ width: min(1120px, 100%); margin: 0 auto; }}
.topbar {{
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  margin-bottom: 22px;
}}
.brand {{
  font-family: 'DM Serif Display', serif;
  font-size: 34px;
  letter-spacing: -0.04em;
  text-decoration: none;
  color: var(--ink);
}}
.brand span {{ color: var(--accent); }}
.topnav {{
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}}
.topnav a {{
  text-decoration: none;
  color: var(--soft);
  font-size: 14px;
  font-weight: 700;
}}
.button {{
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 13px 18px;
  border-radius: 999px;
  font-weight: 800;
  text-decoration: none;
}}
.button.primary {{
  color: white;
  background: linear-gradient(135deg, var(--accent), var(--accent-2));
  box-shadow: 0 18px 36px rgba(239,90,41,0.2);
}}
.button.soft {{
  color: var(--ink);
  background: rgba(255,255,255,0.7);
  border: 1px solid var(--line);
}}
.layout {{
  display: grid;
  grid-template-columns: minmax(0, 1fr) 380px;
  gap: 18px;
  align-items: stretch;
}}
.panel {{
  border-radius: 32px;
  background: var(--card);
  border: 1px solid var(--line);
  box-shadow: 0 28px 70px rgba(96, 67, 45, 0.12);
  backdrop-filter: blur(14px);
}}
.main-panel {{ padding: 30px; }}
.eyebrow {{
  display: inline-flex;
  padding: 10px 14px;
  border-radius: 999px;
  background: rgba(255,255,255,0.76);
  border: 1px solid var(--line);
  color: var(--soft);
  font-size: 13px;
  font-weight: 800;
}}
h1 {{
  margin-top: 16px;
  font-family: 'DM Serif Display', serif;
  font-size: clamp(44px, 6vw, 78px);
  line-height: 0.95;
  letter-spacing: -0.05em;
}}
.lead {{
  margin-top: 16px;
  color: var(--soft);
  font-size: 17px;
  line-height: 1.8;
  max-width: 660px;
}}
.card-row {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-top: 20px;
}}
.info-card {{
  border-radius: 22px;
  padding: 16px;
  background: rgba(255,255,255,0.76);
  border: 1px solid var(--line);
}}
.info-card strong {{
  display: block;
  font-size: 15px;
  margin-bottom: 4px;
}}
.info-card span {{
  color: var(--muted);
  font-size: 13px;
  line-height: 1.55;
}}
.form-panel {{
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}}
.form-panel h2 {{
  font-size: 26px;
  line-height: 1.05;
}}
.form-panel p {{
  color: var(--soft);
  line-height: 1.7;
  font-size: 14px;
}}
.field {{
  display: grid;
  gap: 6px;
}}
.field label {{
  font-size: 13px;
  font-weight: 700;
  color: var(--soft);
}}
.field input, .field select {{
  width: 100%;
  padding: 13px 14px;
  border-radius: 16px;
  border: 1px solid var(--line);
  background: rgba(255,255,255,0.88);
  font: inherit;
  color: var(--ink);
}}
.submit {{
  border: 0;
  cursor: pointer;
  padding: 15px 18px;
  border-radius: 18px;
  background: linear-gradient(135deg, var(--accent), var(--accent-2));
  color: white;
  font: inherit;
  font-weight: 800;
}}
.sub-actions {{
  display: flex;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  align-items: center;
}}
.sub-actions a {{
  color: var(--soft);
  font-size: 13px;
  font-weight: 700;
  text-decoration: none;
}}
.aside-panel {{
  padding: 24px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 16px;
}}
.aside-panel h3 {{
  font-size: 24px;
  line-height: 1.08;
}}
.aside-panel p {{
  color: var(--soft);
  line-height: 1.75;
  font-size: 14px;
}}
.aside-panel ul {{
  display: grid;
  gap: 10px;
  list-style: none;
}}
.aside-panel li {{
  padding: 12px 14px;
  border-radius: 18px;
  background: rgba(255,255,255,0.72);
  border: 1px solid var(--line);
  color: var(--soft);
  font-size: 13px;
  line-height: 1.6;
}}
.note {{
  padding: 14px;
  border-radius: 18px;
  background: rgba(41,93,87,0.1);
  color: var(--green);
  font-size: 13px;
  line-height: 1.65;
}}
@media (max-width: 960px) {{
  .layout {{ grid-template-columns: 1fr; }}
  .card-row {{ grid-template-columns: 1fr; }}
  .topbar {{ flex-direction: column; align-items: flex-start; }}
}}
</style>
</head>
<body>
<div class="page">
  <div class="shell">
    <div class="topbar">
      <a class="brand" href="/">Sin<span>yal</span></a>
      <div class="topnav">
        <a href="/signin">Masuk</a>
        <a href="/signup">Daftar</a>
        <a href="/payment" class="button soft">Lihat Pembayaran</a>
        <a href="/app" class="button primary">Masuk ke App</a>
      </div>
    </div>

    <div class="layout">
      <div class="panel main-panel">
        <div class="eyebrow">{eyebrow}</div>
        <h1>{heading}</h1>
        <p class="lead">{subheading}</p>
        <div class="card-row">
          <div class="info-card">
            <strong>Flow yang saya pilih</strong>
            <span>Masuk, pilih paket, checkout, lalu akses aplikasi aktif otomatis begitu pembayaran masuk.</span>
          </div>
          <div class="info-card">
            <strong>Untuk market Indonesia</strong>
            <span>Bahasa dibuat sederhana, pilihan paket jelas, dan metode bayar diarahkan ke gateway lokal yang lebih cocok.</span>
          </div>
        </div>
      </div>

      <div class="panel form-panel">
        <div>
          <h2>{primary_label}</h2>
          <p>{footer_note}</p>
        </div>
        {form_fields}
        <button class="submit" type="button">{primary_label}</button>
        <div class="sub-actions">
          <a href="{secondary_href}">{secondary_label}</a>
          <a href="/payment">Lihat paket dulu</a>
        </div>
      </div>
    </div>

    <div style="height:18px"></div>

    <div class="panel aside-panel">
      <div>
        <h3>{aside_title}</h3>
        <p>{aside_body}</p>
      </div>
      <ul>{aside_items}</ul>
      <div class="note">{footer_note}</div>
    </div>
  </div>
</div>
{extra_script}
</body>
</html>"""


def render_payment_page():
    plan_cards = []
    for plan in get_plan_catalog():
        featured = " featured" if plan["code"] == "tumbuh" else ""
        badge = "Paling dipilih" if plan["code"] == "tumbuh" else "Langganan"
        button_href = f"/checkout/{plan['code']}"
        button_label = plan["cta"]
        readiness = (
            "Checkout Mayar siap dipakai."
            if plan["checkout_url"]
            else "Belum ada link Mayar di environment. Tinggal isi env lalu aktif."
        )
        limits_html = "".join(f"<li>{item}</li>" for item in plan["limits"])
        plan_cards.append(
            f"""
            <div class="plan-card{featured}">
              <div class="plan-badge">{badge}</div>
              <h3>{plan['name']}</h3>
              <div class="plan-price">{plan['price_label']}<small>/ bulan</small></div>
              <p class="plan-tagline">{plan['tagline']}</p>
              <ul class="plan-list">{limits_html}</ul>
              <a class="plan-button" href="{button_href}">{button_label}</a>
              <div class="plan-note">{readiness}</div>
            </div>
            """
        )

    plans_html = "".join(plan_cards)
    return f"""<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Pembayaran Sinyal</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=DM+Serif+Display:ital@0;1&display=swap" rel="stylesheet">
<style>
:root {{
  --bg: #f7f1e8;
  --ink: #1e1711;
  --soft: #6f5b4b;
  --muted: #9a8779;
  --line: rgba(80, 52, 31, 0.12);
  --card: rgba(255,255,255,0.84);
  --card-strong: rgba(255,255,255,0.94);
  --accent: #ef5a29;
  --accent-2: #ff8d42;
  --green: #2f5f57;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  font-family: 'Plus Jakarta Sans', sans-serif;
  color: var(--ink);
  min-height: 100vh;
  background:
    radial-gradient(circle at top left, rgba(239,90,41,0.16), transparent 28%),
    radial-gradient(circle at top right, rgba(47,95,87,0.14), transparent 26%),
    linear-gradient(180deg, #fffaf4 0%, #f7f1e8 58%, #f0e3d4 100%);
}}
body::before {{
  content: "";
  position: fixed;
  inset: 0;
  pointer-events: none;
  background-image:
    linear-gradient(rgba(80,52,31,0.03) 1px, transparent 1px),
    linear-gradient(90deg, rgba(80,52,31,0.03) 1px, transparent 1px);
  background-size: 34px 34px;
  mask-image: linear-gradient(180deg, rgba(0,0,0,0.65), transparent 90%);
}}
.page {{ position: relative; z-index: 1; padding: 28px 16px 40px; }}
.shell {{ width: min(1180px, 100%); margin: 0 auto; }}
.topbar {{
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  margin-bottom: 24px;
}}
.brand {{
  font-family: 'DM Serif Display', serif;
  font-size: 34px;
  letter-spacing: -0.04em;
  text-decoration: none;
  color: var(--ink);
}}
.brand span {{ color: var(--accent); }}
.topnav {{
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}}
.topnav a {{
  text-decoration: none;
  color: var(--soft);
  font-size: 14px;
  font-weight: 700;
}}
.hero {{
  display: grid;
  grid-template-columns: minmax(0, 1.05fr) minmax(320px, 0.95fr);
  gap: 18px;
  align-items: stretch;
}}
.panel {{
  border-radius: 34px;
  background: var(--card);
  border: 1px solid var(--line);
  box-shadow: 0 28px 70px rgba(95, 67, 45, 0.12);
  backdrop-filter: blur(14px);
}}
.hero-copy {{
  padding: 30px;
}}
.eyebrow {{
  display: inline-flex;
  padding: 10px 14px;
  border-radius: 999px;
  background: rgba(255,255,255,0.76);
  border: 1px solid var(--line);
  color: var(--soft);
  font-size: 13px;
  font-weight: 800;
}}
.hero-copy h1 {{
  margin-top: 16px;
  font-family: 'DM Serif Display', serif;
  font-size: clamp(46px, 6vw, 82px);
  line-height: 0.96;
  letter-spacing: -0.05em;
}}
.hero-copy p {{
  margin-top: 16px;
  color: var(--soft);
  font-size: 17px;
  line-height: 1.8;
  max-width: 640px;
}}
.mini-grid {{
  margin-top: 20px;
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}}
.mini-card {{
  border-radius: 22px;
  padding: 16px;
  background: rgba(255,255,255,0.78);
  border: 1px solid var(--line);
}}
.mini-card strong {{
  display: block;
  font-size: 24px;
  margin-bottom: 4px;
}}
.mini-card span {{
  color: var(--muted);
  font-size: 13px;
  line-height: 1.55;
}}
.aside {{
  padding: 24px;
  display: grid;
  gap: 14px;
}}
.aside h3 {{
  font-size: 26px;
  line-height: 1.06;
}}
.aside p {{
  color: var(--soft);
  font-size: 14px;
  line-height: 1.75;
}}
.aside-box {{
  padding: 14px;
  border-radius: 20px;
  background: rgba(255,255,255,0.76);
  border: 1px solid var(--line);
}}
.aside-box strong {{
  display: block;
  margin-bottom: 6px;
}}
.aside-box span {{
  color: var(--muted);
  font-size: 13px;
  line-height: 1.55;
}}
.pricing {{
  margin-top: 18px;
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}}
.plan-card {{
  padding: 24px;
  border-radius: 30px;
  background: var(--card-strong);
  border: 1px solid var(--line);
  box-shadow: 0 18px 48px rgba(95, 67, 45, 0.08);
}}
.plan-card.featured {{
  background: linear-gradient(180deg, rgba(239,90,41,0.14), rgba(255,255,255,0.97));
  border-color: rgba(239,90,41,0.24);
}}
.plan-badge {{
  display: inline-flex;
  padding: 8px 12px;
  border-radius: 999px;
  background: rgba(239,90,41,0.1);
  color: var(--accent);
  font-size: 12px;
  font-weight: 800;
  margin-bottom: 12px;
}}
.plan-card h3 {{
  font-size: 25px;
  margin-bottom: 8px;
}}
.plan-price {{
  font-family: 'DM Serif Display', serif;
  font-size: 44px;
  letter-spacing: -0.05em;
}}
.plan-price small {{
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 14px;
  color: var(--muted);
}}
.plan-tagline {{
  margin: 12px 0 14px;
  color: var(--soft);
  line-height: 1.7;
}}
.plan-list {{
  list-style: none;
  display: grid;
  gap: 9px;
  color: var(--soft);
  font-size: 14px;
  line-height: 1.65;
  min-height: 160px;
}}
.plan-button {{
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  margin-top: 14px;
  padding: 14px 16px;
  border-radius: 18px;
  text-decoration: none;
  font-weight: 800;
  color: white;
  background: linear-gradient(135deg, var(--accent), var(--accent-2));
  box-shadow: 0 16px 32px rgba(239,90,41,0.18);
}}
.plan-note {{
  margin-top: 12px;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.6;
}}
.footnote {{
  margin-top: 18px;
  padding: 16px 18px;
  border-radius: 20px;
  background: rgba(47,95,87,0.1);
  color: var(--green);
  font-size: 13px;
  line-height: 1.7;
}}
.after-pay {{
  margin-top: 18px;
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
}}
.after-pay-card {{
  padding: 18px;
  border-radius: 24px;
  background: rgba(255,255,255,0.82);
  border: 1px solid var(--line);
}}
.after-pay-card strong {{
  display: block;
  margin-bottom: 6px;
  font-size: 16px;
}}
.after-pay-card span {{
  color: var(--soft);
  font-size: 13px;
  line-height: 1.65;
}}
@media (max-width: 980px) {{
  .hero, .pricing, .mini-grid, .after-pay {{ grid-template-columns: 1fr; }}
  .topbar {{ flex-direction: column; align-items: flex-start; }}
}}
</style>
</head>
<body>
<div class="page">
  <div class="shell">
    <div class="topbar">
      <a class="brand" href="/">Sin<span>yal</span></a>
      <div class="topnav">
        <a href="/signin">Masuk</a>
        <a href="/signup">Daftar</a>
        <a href="/app">Buka App</a>
      </div>
    </div>

    <div class="hero">
      <div class="panel hero-copy">
        <div class="eyebrow">Checkout akses Sinyal</div>
        <h1>Satu halaman bayar yang rapi, jelas, dan siap untuk market Indonesia.</h1>
        <p>Saya arahkan payment MVP ke Mayar dulu supaya kita bisa launch cepat. User pilih paket, masuk ke checkout hosted, lalu akses aktif begitu webhook pembayaran masuk ke backend dan database.</p>
        <div class="mini-grid">
          <div class="mini-card"><strong>Mayar</strong><span>Checkout hosted yang lebih cepat dipakai untuk MVP.</span></div>
          <div class="mini-card"><strong>Supabase</strong><span>Auth + session + Postgres di satu stack yang ringkas.</span></div>
          <div class="mini-card"><strong>Webhook</strong><span>Status bayar masuk ke subscription dan akses user otomatis.</span></div>
        </div>
      </div>

      <div class="panel aside">
        <div>
          <h3>Yang terjadi setelah user klik bayar</h3>
          <p>Bukan cuma invoice link. Flow backend-nya tetap saya pikirkan sebagai produk SaaS sungguhan.</p>
        </div>
        <div class="aside-box">
          <strong>1. Redirect ke Mayar</strong>
          <span>User dibawa ke hosted checkout atau product page yang sesuai dengan paket yang dipilih.</span>
        </div>
        <div class="aside-box">
          <strong>2. Webhook masuk</strong>
          <span>Backend mencatat transaksi, update invoice, dan aktifkan subscription di Postgres.</span>
        </div>
        <div class="aside-box">
          <strong>3. App kasih akses</strong>
          <span>Quota dan fitur dibaca dari plan aktif user, bukan dari frontend semata.</span>
        </div>
      </div>
    </div>

    <div class="pricing">
      {plans_html}
    </div>

    <div class="after-pay">
      <div class="after-pay-card">
        <strong>Akun langsung dicek</strong>
        <span>Begitu user selesai bayar, backend baca invoice dan update langganan di database.</span>
      </div>
      <div class="after-pay-card">
        <strong>Akses otomatis kebuka</strong>
        <span>Search, profile, dan comments tidak dibuka manual. Semua dibaca dari status paket aktif.</span>
      </div>
      <div class="after-pay-card">
        <strong>Kuota tercatat rapi</strong>
        <span>Pemakaian per fitur langsung masuk ke usage log, jadi nanti gampang dipantau dan dibatasi per plan.</span>
      </div>
    </div>

    <div class="footnote">
      Payment awal saya arahkan ke Mayar karena paling cepat untuk launch. Begitu volume naik dan kita butuh kontrol billing yang lebih dalam, flow ini masih bisa dipindah ke gateway direct tanpa buang fondasi auth dan Postgres.
    </div>
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
<title>Lanjutkan Setup Sinyal</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=DM+Serif+Display:ital@0;1&display=swap" rel="stylesheet">
<style>
:root {
  --bg: #f7efe4;
  --ink: #1f1711;
  --soft: #725a4b;
  --muted: #9b8576;
  --line: rgba(80, 52, 31, 0.12);
  --card: rgba(255,255,255,0.88);
  --accent: #ef5a29;
  --accent-2: #ff8d42;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: 'Plus Jakarta Sans', sans-serif;
  color: var(--ink);
  min-height: 100vh;
  background:
    radial-gradient(circle at top left, rgba(239,90,41,0.16), transparent 28%),
    radial-gradient(circle at top right, rgba(41,93,87,0.14), transparent 24%),
    linear-gradient(180deg, #fffaf4 0%, #f7efe4 56%, #f0e2d2 100%);
}
.page {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 24px;
}
.shell {
  width: min(760px, 100%);
  display: grid;
  gap: 18px;
}
.brand {
  font-family: 'DM Serif Display', serif;
  font-size: 34px;
  letter-spacing: -0.04em;
  text-decoration: none;
  color: var(--ink);
}
.brand span { color: var(--accent); }
.panel {
  border-radius: 32px;
  background: var(--card);
  border: 1px solid var(--line);
  box-shadow: 0 28px 70px rgba(96, 67, 45, 0.12);
  backdrop-filter: blur(14px);
  padding: 28px;
}
.eyebrow {
  display: inline-flex;
  padding: 10px 14px;
  border-radius: 999px;
  background: rgba(255,255,255,0.76);
  border: 1px solid var(--line);
  color: var(--soft);
  font-size: 13px;
  font-weight: 800;
}
h1 {
  margin-top: 16px;
  font-family: 'DM Serif Display', serif;
  font-size: clamp(42px, 7vw, 72px);
  line-height: 0.98;
  letter-spacing: -0.05em;
}
p.lead {
  margin-top: 14px;
  color: var(--soft);
  font-size: 17px;
  line-height: 1.8;
}
.steps {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-top: 22px;
}
.step {
  padding: 16px;
  border-radius: 22px;
  background: rgba(255,255,255,0.76);
  border: 1px solid var(--line);
}
.step strong {
  display: block;
  margin-bottom: 6px;
  font-size: 15px;
}
.step span {
  color: var(--muted);
  font-size: 13px;
  line-height: 1.6;
}
.status-card {
  padding: 18px;
  border-radius: 24px;
  background: rgba(255,255,255,0.78);
  border: 1px solid var(--line);
}
.status-card strong {
  display: block;
  font-size: 21px;
  margin-bottom: 8px;
}
.status-card p {
  color: var(--soft);
  font-size: 14px;
  line-height: 1.75;
}
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 16px;
}
.button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 14px 18px;
  border-radius: 999px;
  text-decoration: none;
  font-weight: 800;
}
.button.primary {
  color: white;
  background: linear-gradient(135deg, var(--accent), var(--accent-2));
  box-shadow: 0 18px 36px rgba(239,90,41,0.2);
}
.button.soft {
  color: var(--ink);
  background: rgba(255,255,255,0.7);
  border: 1px solid var(--line);
}
@media (max-width: 760px) {
  .steps { grid-template-columns: 1fr; }
}
</style>
</head>
<body>
<div class="page">
  <div class="shell">
    <a class="brand" href="/">Sin<span>yal</span></a>
    <div class="panel">
      <div class="eyebrow">Lanjutkan setup akun</div>
      <h1>Tinggal satu langkah lagi buat mulai kerja.</h1>
      <p class="lead">Halaman ini bantu orang lanjut ke langkah berikutnya tanpa bingung: bikin akun, aktifkan paket, lalu langsung masuk ke workspace riset.</p>
      <div class="steps">
        <div class="step"><strong>1. Bikin akun</strong><span>Masuk cepat supaya hasil riset dan aktivitasmu tersimpan rapi.</span></div>
        <div class="step"><strong>2. Aktifkan akses</strong><span>Pilih paket yang paling cocok biar semua fitur utama bisa dipakai.</span></div>
        <div class="step"><strong>3. Mulai riset</strong><span>Begitu siap, langsung masuk ke app dan cari pola konten yang lagi jalan.</span></div>
      </div>
    </div>
    <div class="panel status-card">
      <strong id="nextStepTitle">Sedang menyiapkan langkah berikutnya...</strong>
      <p id="nextStepBody">Tunggu sebentar, kami lagi lihat langkah paling pas buat kamu lanjut.</p>
      <div class="actions">
        <a class="button primary" id="nextStepButton" href="/app">Lanjut</a>
        <a class="button soft" href="/">Kembali ke landing</a>
      </div>
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
    body.textContent = 'Kalau langkah otomatis belum kebaca, kamu tetap bisa lanjut manual ke app atau ke halaman paket.';
    button.textContent = 'Buka app';
    button.href = '/app';
  }
}
loadNextStep();
</script>
</body>
</html>"""


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
        <div class="field"><label>Nama lengkap</label><input id="signupFullName" type="text" placeholder="Nama kamu atau nama tim"></div>
        <div class="field"><label>Nama usaha / tim</label><input id="signupCompanyName" type="text" placeholder="Nama brand atau agency"></div>
        <div class="field"><label>Email kerja</label><input id="signupEmail" type="email" placeholder="nama@brand.com"></div>
        <div class="field"><label>Password</label><input id="signupPassword" type="password" placeholder="Minimal 8 karakter"></div>
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
          status.textContent = 'Lagi bikin akun...';
          const payload = {
            full_name: document.getElementById('signupFullName').value,
            company_name: document.getElementById('signupCompanyName').value,
            email: document.getElementById('signupEmail').value,
            password: document.getElementById('signupPassword').value,
            onboarding_use_case: document.getElementById('signupUseCase').value,
          };
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
          status.textContent = 'Lagi masuk...';
          const payload = {
            email: document.getElementById('signinEmail').value,
            password: document.getElementById('signinPassword').value,
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
    response = JSONResponse(data, status_code=status_code)
    session = data.get("session") or {}
    access_token = session.get("access_token")
    refresh_token = session.get("refresh_token")
    if access_token:
        response.set_cookie(AUTH_COOKIE_NAME, access_token, httponly=True, samesite="lax", secure=COOKIE_SECURE, max_age=60 * 60 * 24 * 7)
    if refresh_token:
        response.set_cookie(REFRESH_COOKIE_NAME, refresh_token, httponly=True, samesite="lax", secure=COOKIE_SECURE, max_age=60 * 60 * 24 * 30)
    user = data.get("user") or {}
    user_id = user.get("id")
    if user_id and supabase_rest_configured():
        await supabase_rest_request(
            "PATCH",
            "/rest/v1/profiles",
            params={"user_id": f"eq.{user_id}"},
            payload={
                "full_name": full_name or None,
                "company_name": company_name or None,
                "onboarding_use_case": onboarding_use_case or None,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            prefer="return=minimal",
        )
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
    access_token = data.get("access_token")
    refresh_token = data.get("refresh_token")
    if access_token:
        response.set_cookie(AUTH_COOKIE_NAME, access_token, httponly=True, samesite="lax", secure=COOKIE_SECURE, max_age=60 * 60 * 24 * 7)
    if refresh_token:
        response.set_cookie(REFRESH_COOKIE_NAME, refresh_token, httponly=True, samesite="lax", secure=COOKIE_SECURE, max_age=60 * 60 * 24 * 30)
    return response


@app.post("/api/auth/signout")
async def auth_signout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(AUTH_COOKIE_NAME)
    response.delete_cookie(REFRESH_COOKIE_NAME)
    return response


@app.get("/api/auth/session")
async def auth_session(request: Request):
    if not supabase_auth_configured():
        return {"configured": False, "authenticated": False}
    user = await get_authenticated_user(request)
    subscription = plan = None
    if user and supabase_rest_configured():
        subscription, plan = await fetch_current_subscription(user["id"])
    return {
        "configured": True,
        "authenticated": bool(user),
        "user": user,
        "subscription": subscription,
        "plan": plan,
    }


@app.get("/api/system/config")
async def system_config():
    plans = get_plan_catalog()
    return {
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
    if not supabase_auth_configured():
        return {"configured": False}

    user = await get_authenticated_user(request)
    if not user:
        return JSONResponse({"error": "Silakan login dulu."}, status_code=401)

    if not supabase_rest_configured():
        return {"configured": True, "database_ready": False, "user": user}

    subscription, plan = await fetch_current_subscription(user["id"])
    period_start = usage_period_start(subscription)
    counters = {}
    for event_type in ("search", "profile", "comments", "transcript"):
        counters[event_type] = await get_usage_total(user["id"], event_type, period_start)

    return {
        "configured": True,
        "database_ready": True,
        "user": user,
        "subscription": subscription,
        "plan": plan,
        "period_start": period_start.isoformat(),
        "usage": counters,
    }


@app.get("/api/account/next-step")
async def account_next_step(request: Request):
    if not supabase_auth_configured():
        return {
            "configured": False,
            "target": "/app",
            "title": "Workspace siap dibuka",
            "message": "Kamu bisa langsung masuk ke app dan mulai eksplor workflow riset yang ada sekarang.",
            "cta_label": "Buka app",
        }

    user = await get_authenticated_user(request)
    if not user:
        return {
            "configured": True,
            "authenticated": False,
            "target": "/signin",
            "title": "Masuk dulu",
            "message": "Akun belum aktif di browser ini. Masuk dulu supaya sistem bisa cek paket dan kuota kamu.",
            "cta_label": "Masuk",
        }

    subscription = plan = None
    if supabase_rest_configured():
        subscription, plan = await fetch_current_subscription(user["id"])

    if subscription_is_active(subscription):
        return {
            "configured": True,
            "authenticated": True,
            "target": "/app",
            "title": "Akun siap dipakai",
            "message": f"Paket {plan.get('name') if plan else subscription.get('plan_code')} aktif. Kamu bisa lanjut langsung ke app.",
            "cta_label": "Masuk ke app",
            "plan": plan,
            "subscription": subscription,
        }

    return {
        "configured": True,
        "authenticated": True,
        "target": "/payment",
        "title": "Pilih paket dulu",
        "message": "Akun sudah jadi, tapi paket aktif belum ada. Langkah berikutnya tinggal pilih paket supaya akses fitur terbuka.",
        "cta_label": "Lihat paket",
        "plan": plan,
        "subscription": subscription,
    }


@app.post("/api/payment/webhook/mayar")
async def mayar_webhook(request: Request, payload: dict = Body(...)):
    if not mayar_secret_matches(request):
        return JSONResponse({"error": "Webhook secret tidak valid."}, status_code=401)

    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    amount = int(data.get("amount") or payload.get("amount") or 0)
    payer_email = normalize_text(
        data.get("customerEmail")
        or data.get("email")
        or payload.get("customerEmail")
        or payload.get("email")
    ).lower()
    provider_invoice_id = str(
        data.get("invoiceId")
        or data.get("id")
        or payload.get("invoiceId")
        or payload.get("id")
        or ""
    ).strip() or None
    provider_payment_id = str(
        data.get("transactionId")
        or data.get("paymentId")
        or payload.get("transactionId")
        or payload.get("paymentId")
        or ""
    ).strip() or None
    checkout_url = (
        data.get("paymentUrl")
        or data.get("invoiceUrl")
        or payload.get("paymentUrl")
        or payload.get("invoiceUrl")
        or ""
    )
    raw_status = normalize_text(
        data.get("status")
        or payload.get("status")
        or payload.get("event")
        or ""
    ).lower()
    product_name = normalize_text(
        data.get("productName")
        or payload.get("productName")
        or data.get("description")
        or payload.get("description")
    )
    paid_at = (
        parse_epoch_millis(data.get("updatedAt"))
        or parse_epoch_millis(data.get("paidAt"))
        or parse_epoch_millis(payload.get("updatedAt"))
        or parse_epoch_millis(payload.get("paidAt"))
    )

    payment_status = "pending"
    subscription_status = "pending"
    if raw_status in {"paid", "success", "settled", "completed", "true"}:
        payment_status = "paid"
        subscription_status = "active"
    elif raw_status in {"failed", "expired", "cancelled", "canceled"}:
        payment_status = "failed" if raw_status == "failed" else "expired"
        subscription_status = "expired" if raw_status == "expired" else "cancelled"

    plan_code = infer_plan_code(product_name, amount)
    profile = await fetch_profile_by_email(payer_email) if payer_email else None
    subscription = None
    if profile and plan_code:
        subscription = await upsert_subscription_record(
            user_id=profile["user_id"],
            plan_code=plan_code,
            provider_invoice_id=provider_invoice_id,
            status=subscription_status,
            paid_at=paid_at,
        )

    transaction = await upsert_payment_transaction(
        {
            "user_id": profile.get("user_id") if profile else None,
            "subscription_id": subscription.get("id") if subscription else None,
            "provider": "mayar",
            "provider_invoice_id": provider_invoice_id,
            "provider_payment_id": provider_payment_id,
            "checkout_url": checkout_url or None,
            "amount_idr": amount,
            "currency": normalize_text(data.get("currency") or payload.get("currency") or "IDR") or "IDR",
            "status": payment_status,
            "payer_email": payer_email or None,
            "raw_payload": payload,
            "paid_at": paid_at.isoformat() if paid_at else None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )

    return {
        "received": True,
        "provider": "mayar",
        "payment_status": payment_status,
        "subscription_status": subscription_status,
        "plan_code": plan_code,
        "profile_found": bool(profile),
        "subscription_id": subscription.get("id") if subscription else None,
        "transaction_id": transaction.get("id") if transaction else None,
    }

@app.get("/app", response_class=HTMLResponse)
async def app_page(request: Request):
    # Allow unauthenticated access for FREE tier
    return APP_HTML


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
        return {
            **cached[1],
            "cached": True,
            "elapsed": "<1s (cached)",
            "json_file": cached[1].get("json_file"),
            "csv_file": cached[1].get("csv_file"),
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

    json_file = csv_file = None
    watermark = bool(plan and plan.get("watermark_exports"))
    if all_results:
        json_file, csv_file = save_results(all_results, keywords[0], watermark=watermark)

    payload = {
        "keywords": keywords,
        "platforms": platform_list,
        "total": len(all_results),
        "elapsed": f"{elapsed:.1f}s",
        "cached": False,
        "plan_code": plan.get("code") if plan else None,
        "json_file": json_file,
        "csv_file": csv_file,
        "results": [r.to_dict() for r in all_results],
    }
    SEARCH_CACHE[cache_key] = (time.time(), payload)
    if user and supabase_rest_configured():
        await record_usage_event(
            user["id"],
            "search",
            {
                "keywords": keywords,
                "platforms": platform_list,
                "total_results": len(all_results),
            },
        )
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

    max_value = max_results or max or 10

    cache_key = (username.lstrip("@").lower(), max_value, sort, date_range)
    cached = PROFILE_CACHE.get(cache_key)
    if cached and time.time() - cached[0] < PROFILE_CACHE_TTL_SECONDS:
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
    json_file = csv_file = None
    if results:
        json_file, csv_file = save_results(results, f"profile_{username}")

    payload = {
        "username": username,
        "total": len(results),
        "elapsed": f"{elapsed:.1f}s",
        "cached": False,
        "plan_code": plan.get("code") if plan else None,
        "json_file": json_file,
        "csv_file": csv_file,
        "results": [r.to_dict() for r in results],
    }
    PROFILE_CACHE[cache_key] = (time.time(), payload)
    if user and supabase_rest_configured():
        await record_usage_event(
            user["id"],
            "profile",
            {
                "username": username.lstrip("@"),
                "total_results": len(results),
            },
        )
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
        if parsed and parsed >= cutoff:
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

    target_url = (url or video_url or "").strip()
    if not target_url:
        return JSONResponse({"error": "URL video wajib diisi via `url` atau `video_url`."}, 400)

    if platform and platform.lower() != "tiktok":
        return JSONResponse({"error": "Comments scraping saat ini baru support TikTok."}, 400)

    max_value = max_comments or max or 50

    cache_key = (target_url, max_value)
    cached = COMMENTS_CACHE.get(cache_key)
    if cached and time.time() - cached[0] < COMMENTS_CACHE_TTL_SECONDS:
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
    if user and supabase_rest_configured():
        await record_usage_event(
            user["id"],
            "comments",
            {
                "url": target_url,
                "requested_max": max_value,
                "total_results": len(result),
                "video_comment_count": video_comment_count,
            },
        )
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

    safe_path = OUTPUT_DIR / candidate
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
  --bg: #f8f0e4;
  --bg-soft: #fff9f2;
  --ink: #20160f;
  --soft: #705b4c;
  --muted: #9a8474;
  --line: rgba(84, 52, 29, 0.12);
  --card: rgba(255, 250, 244, 0.82);
  --card-strong: rgba(255, 255, 255, 0.92);
  --accent: #ef5a29;
  --accent-2: #ff8d42;
  --accent-soft: rgba(239, 90, 41, 0.12);
  --green: #285f58;
  --green-soft: rgba(40, 95, 88, 0.12);
  --radius-xl: 34px;
  --radius-lg: 28px;
  --radius-md: 22px;
  --shadow: 0 30px 70px rgba(98, 66, 43, 0.12);
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html { scroll-behavior: smooth; }
body {
  font-family: 'Plus Jakarta Sans', sans-serif;
  color: var(--ink);
  background:
    radial-gradient(circle at top left, rgba(239, 90, 41, 0.15), transparent 28%),
    radial-gradient(circle at top right, rgba(40, 95, 88, 0.14), transparent 26%),
    linear-gradient(180deg, #fffaf4 0%, #f8f0e4 58%, #f1e5d8 100%);
}
body::before {
  content: "";
  position: fixed;
  inset: 0;
  pointer-events: none;
  background-image:
    linear-gradient(rgba(84, 52, 29, 0.03) 1px, transparent 1px),
    linear-gradient(90deg, rgba(84, 52, 29, 0.03) 1px, transparent 1px);
  background-size: 36px 36px;
  mask-image: linear-gradient(180deg, rgba(0,0,0,0.72), transparent 88%);
}
.page { position: relative; z-index: 1; }
.container { width: min(1180px, calc(100% - 32px)); margin: 0 auto; }
.glass {
  background: var(--card);
  border: 1px solid var(--line);
  box-shadow: var(--shadow);
  backdrop-filter: blur(14px);
}
nav {
  position: sticky;
  top: 0;
  z-index: 30;
  background: rgba(255, 250, 244, 0.7);
  backdrop-filter: blur(18px);
  border-bottom: 1px solid rgba(84, 52, 29, 0.08);
}
.nav-inner {
  min-height: 76px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
}
.brand {
  font-family: 'DM Serif Display', serif;
  font-size: 34px;
  letter-spacing: -0.04em;
}
.brand span { color: var(--accent); }
.nav-links {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
}
.nav-links .link-group,
.nav-links .cta-group {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.nav-links a {
  text-decoration: none;
  color: var(--soft);
  font-size: 14px;
  font-weight: 700;
}
.button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 14px 20px;
  border-radius: 999px;
  text-decoration: none;
  font-weight: 800;
  transition: transform 0.18s ease, box-shadow 0.18s ease, background 0.18s ease;
}
.button:hover { transform: translateY(-1px); }
.button.primary {
  background: linear-gradient(135deg, var(--accent), var(--accent-2));
  color: white;
  box-shadow: 0 18px 36px rgba(239, 90, 41, 0.22);
}
.button.secondary {
  color: var(--ink);
  background: rgba(255, 255, 255, 0.72);
  border: 1px solid var(--line);
}
.hero {
  padding: 44px 0 30px;
}
.hero-shell {
  display: grid;
  grid-template-columns: minmax(0, 1.02fr) minmax(320px, 0.98fr);
  gap: 22px;
  align-items: stretch;
}
.hero-copy {
  padding: 18px 4px 8px 0;
}
.eyebrow {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border-radius: 999px;
  background: rgba(255,255,255,0.74);
  border: 1px solid var(--line);
  color: var(--soft);
  font-size: 13px;
  font-weight: 800;
}
.eyebrow::before {
  content: "";
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: linear-gradient(135deg, var(--accent), var(--accent-2));
}
.hero h1 {
  margin-top: 18px;
  font-family: 'DM Serif Display', serif;
  font-size: clamp(50px, 7vw, 94px);
  line-height: 0.92;
  letter-spacing: -0.05em;
}
.hero h1 em {
  font-style: italic;
  color: var(--accent);
}
.hero p {
  margin-top: 18px;
  max-width: 640px;
  color: var(--soft);
  font-size: 18px;
  line-height: 1.78;
}
.hero-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 28px;
  align-items: center;
}
.hero-strip {
  margin-top: 18px;
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
.mini-pill {
  padding: 10px 14px;
  border-radius: 999px;
  background: rgba(255,255,255,0.62);
  border: 1px solid var(--line);
  color: var(--soft);
  font-size: 13px;
  font-weight: 700;
}
.hero-ui {
  border-radius: 34px;
  overflow: hidden;
}
.hero-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 18px 20px 12px;
}
.hero-topbar strong {
  font-size: 14px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--muted);
}
.toggle-group {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.toggle {
  border: 0;
  cursor: pointer;
  padding: 10px 14px;
  border-radius: 999px;
  background: rgba(239, 90, 41, 0.08);
  color: var(--soft);
  font-weight: 800;
  font-size: 13px;
}
.toggle.active {
  background: linear-gradient(135deg, var(--accent), var(--accent-2));
  color: white;
}
.showcase {
  padding: 0 20px 20px;
}
.preview-stack {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 168px;
  gap: 12px;
}
.search-shell {
  border-radius: 28px;
  background: rgba(255,255,255,0.86);
  border: 1px solid rgba(84, 52, 29, 0.08);
  padding: 16px;
}
.search-input {
  width: 100%;
  border-radius: 18px;
  border: 1px solid rgba(84, 52, 29, 0.08);
  background: #fff;
  padding: 16px 18px;
  font-size: 15px;
  color: var(--ink);
  margin-bottom: 14px;
}
.filter-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 14px;
}
.chip {
  padding: 9px 12px;
  border-radius: 999px;
  background: var(--bg-soft);
  border: 1px solid rgba(84, 52, 29, 0.08);
  color: var(--soft);
  font-size: 12px;
  font-weight: 800;
}
.data-grid {
  display: grid;
  grid-template-columns: 1.2fr 0.8fr;
  gap: 12px;
}
.results-column, .metric-column {
  display: grid;
  gap: 12px;
}
.right-rail {
  display: grid;
  gap: 12px;
}
.rail-panel {
  border-radius: 22px;
  background: #fff;
  border: 1px solid rgba(84, 52, 29, 0.08);
  padding: 14px;
}
.rail-panel h4 {
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--muted);
  margin-bottom: 10px;
}
.rail-score {
  display: grid;
  gap: 10px;
}
.score-item {
  padding: 10px 12px;
  border-radius: 16px;
  background: rgba(239, 90, 41, 0.08);
  border: 1px solid rgba(239, 90, 41, 0.12);
}
.score-item strong {
  display: block;
  font-size: 17px;
  margin-bottom: 2px;
}
.score-item span {
  color: var(--soft);
  font-size: 12px;
  line-height: 1.5;
}
.mini-feed {
  display: grid;
  gap: 10px;
}
.mini-video {
  min-height: 100px;
  border-radius: 18px;
  padding: 12px;
  background:
    linear-gradient(180deg, rgba(0,0,0,0.06), rgba(0,0,0,0.18)),
    linear-gradient(135deg, rgba(239, 90, 41, 0.36), rgba(255, 179, 71, 0.18), rgba(255,255,255,0.96));
  border: 1px solid rgba(84, 52, 29, 0.08);
  display: flex;
  flex-direction: column;
  justify-content: space-between;
}
.mini-video strong {
  color: white;
  font-size: 13px;
  line-height: 1.45;
  text-shadow: 0 2px 10px rgba(0,0,0,0.25);
}
.mini-video span {
  color: rgba(255,255,255,0.88);
  font-size: 11px;
  font-weight: 700;
}
.result-card, .metric-card {
  border-radius: 22px;
  background: #fff;
  border: 1px solid rgba(84, 52, 29, 0.08);
  padding: 14px;
}
.result-card strong, .metric-card strong {
  display: block;
  font-size: 15px;
  margin-bottom: 4px;
}
.result-card span, .metric-card span {
  color: var(--muted);
  font-size: 13px;
  line-height: 1.55;
}
.metric-card.highlight {
  background: linear-gradient(135deg, rgba(239, 90, 41, 0.12), rgba(255,255,255,0.96));
}
.stats-row {
  margin-top: 14px;
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}
.stat-box {
  border-radius: 20px;
  padding: 14px;
  background: rgba(255,255,255,0.72);
  border: 1px solid var(--line);
}
.stat-box strong {
  display: block;
  font-size: 28px;
  margin-bottom: 4px;
}
.stat-box span {
  color: var(--muted);
  font-size: 13px;
}
section { padding: 30px 0; }
.section-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 18px;
  margin-bottom: 20px;
}
.section-head h2 {
  font-family: 'DM Serif Display', serif;
  font-size: clamp(34px, 4vw, 54px);
  line-height: 0.95;
  letter-spacing: -0.04em;
}
.section-head p {
  max-width: 520px;
  color: var(--soft);
  line-height: 1.75;
}
.quick-proof {
  margin-top: 10px;
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}
.proof-card {
  padding: 16px;
  border-radius: 20px;
  background: rgba(255,255,255,0.78);
  border: 1px solid rgba(84, 52, 29, 0.08);
}
.proof-card strong {
  display: block;
  font-size: 24px;
  margin-bottom: 4px;
}
.proof-card span {
  color: var(--muted);
  font-size: 13px;
  line-height: 1.55;
}
.value-grid {
  display: grid;
  grid-template-columns: 1.08fr 0.92fr 0.92fr;
  gap: 16px;
}
.value-card {
  padding: 24px;
  border-radius: 30px;
}
.value-card h3 {
  font-size: 22px;
  margin-bottom: 10px;
}
.value-card p {
  color: var(--soft);
  line-height: 1.72;
}
.value-card.big {
  background: linear-gradient(135deg, rgba(239, 90, 41, 0.12), rgba(255,255,255,0.94));
}
.stack-list {
  margin-top: 16px;
  display: grid;
  gap: 10px;
}
.stack-item {
  padding: 12px 14px;
  border-radius: 18px;
  background: rgba(255,255,255,0.78);
  border: 1px solid rgba(84, 52, 29, 0.08);
  font-size: 14px;
  color: var(--soft);
  line-height: 1.6;
}
.mode-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 16px;
}
.mode-tab {
  padding: 12px 16px;
  border-radius: 999px;
  border: 1px solid var(--line);
  background: rgba(255,255,255,0.72);
  color: var(--soft);
  font-weight: 800;
  cursor: pointer;
}
.mode-tab.active {
  background: var(--green);
  border-color: var(--green);
  color: white;
}
.scenario-panel {
  display: none;
  grid-template-columns: 0.95fr 1.05fr;
  gap: 14px;
  padding: 18px;
  border-radius: 30px;
}
.scenario-panel.active {
  display: grid;
}
.scenario-copy {
  padding: 10px;
}
.scenario-copy h3 {
  font-size: 26px;
  line-height: 1.08;
  margin-bottom: 10px;
}
.scenario-copy p {
  color: var(--soft);
  line-height: 1.75;
}
.scenario-points {
  margin-top: 16px;
  display: grid;
  gap: 10px;
}
.scenario-point {
  padding: 12px 14px;
  border-radius: 18px;
  background: rgba(255,255,255,0.7);
  border: 1px solid rgba(84, 52, 29, 0.08);
  color: var(--soft);
  font-size: 14px;
  line-height: 1.6;
}
.scenario-shot {
  padding: 18px;
  border-radius: 24px;
  background: rgba(255,255,255,0.76);
  border: 1px solid rgba(84, 52, 29, 0.08);
}
.shot-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.shot-header strong {
  font-size: 16px;
}
.shot-label {
  font-size: 12px;
  color: var(--muted);
  font-weight: 800;
  text-transform: uppercase;
}
.feed-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}
.feed-card {
  min-height: 120px;
  border-radius: 20px;
  padding: 12px;
  background: linear-gradient(180deg, rgba(239, 90, 41, 0.12), rgba(255,255,255,0.94));
  border: 1px solid rgba(84, 52, 29, 0.08);
}
.feed-card strong {
  display: block;
  font-size: 14px;
  margin-bottom: 6px;
}
.feed-card span {
  color: var(--muted);
  font-size: 12px;
  line-height: 1.55;
}
.pricing-wrap {
  padding: 26px;
  border-radius: 36px;
}
.pricing-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}
.price-card {
  padding: 22px;
  border-radius: 28px;
  background: rgba(255,255,255,0.88);
  border: 1px solid var(--line);
}
.price-card.featured {
  background: linear-gradient(180deg, rgba(239, 90, 41, 0.14), rgba(255,255,255,0.96));
  border-color: rgba(239, 90, 41, 0.22);
}
.badge {
  display: inline-flex;
  padding: 8px 12px;
  border-radius: 999px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 12px;
  font-weight: 800;
  margin-bottom: 12px;
}
.price-card h3 {
  font-size: 24px;
  margin-bottom: 8px;
}
.price {
  font-family: 'DM Serif Display', serif;
  font-size: 44px;
  letter-spacing: -0.05em;
}
.price small {
  font-family: 'Plus Jakarta Sans', sans-serif;
  font-size: 14px;
  color: var(--muted);
}
.price-note {
  margin: 12px 0 14px;
  color: var(--soft);
  line-height: 1.7;
}
.price-list {
  display: grid;
  gap: 8px;
  color: var(--soft);
  font-size: 14px;
  line-height: 1.65;
}
.price-cta {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  margin-top: 16px;
  padding: 13px 16px;
  border-radius: 18px;
  text-decoration: none;
  color: white;
  font-weight: 800;
  background: linear-gradient(135deg, var(--accent), var(--accent-2));
  box-shadow: 0 16px 32px rgba(239, 90, 41, 0.18);
}
.cta-panel {
  padding: 28px;
  border-radius: 34px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  background: linear-gradient(135deg, rgba(239, 90, 41, 0.16), rgba(40, 95, 88, 0.12));
}
.cta-panel h2 {
  font-family: 'DM Serif Display', serif;
  font-size: 42px;
  line-height: 0.98;
  letter-spacing: -0.04em;
  margin-bottom: 10px;
}
.cta-panel p {
  color: var(--soft);
  line-height: 1.72;
  max-width: 640px;
}
footer {
  padding: 26px 0 48px;
  color: var(--muted);
  font-size: 14px;
}
@media (max-width: 980px) {
  .hero-shell, .value-grid, .pricing-grid, .scenario-panel, .data-grid, .preview-stack, .quick-proof { grid-template-columns: 1fr; }
  .section-head, .cta-panel, .nav-inner { flex-direction: column; align-items: flex-start; }
  .stats-row { grid-template-columns: 1fr; }
}
@media (max-width: 720px) {
  .hero h1 { font-size: 54px; }
  .feed-grid { grid-template-columns: 1fr; }
  .toggle-group, .nav-links, .nav-links .link-group, .nav-links .cta-group { width: 100%; }
  .toggle, .mode-tab { flex: 1; text-align: center; }
  .button { width: 100%; }
}
</style>
</head>
<body>
<div class="page">
  <nav>
    <div class="container nav-inner">
      <div class="brand">Sin<span>yal</span></div>
      <div class="nav-links">
        <div class="link-group">
          <a href="#nilai">Kenapa enak dipakai</a>
          <a href="#pakai">Contoh pakai</a>
          <a href="#harga">Harga</a>
          <a href="/signin">Masuk</a>
        </div>
        <div class="cta-group">
          <a class="button secondary" href="/payment">Lihat paket</a>
          <a class="button primary" href="/signup">Coba gratis dulu</a>
        </div>
      </div>
    </div>
  </nav>

  <header class="hero">
    <div class="container hero-shell">
      <div class="hero-copy">
        <div class="eyebrow">Dibuat khusus buat cari sinyal sosial media di Indonesia</div>
        <h1>Kalau lagi cari topik yang <em>lagi rame</em>, jangan buka lima tab sekaligus.</h1>
        <p>Sinyal bantu kamu bedah hook, caption, komentar, dan isi video publik dari TikTok, Instagram, X, dan Facebook dalam satu tempat. Fokusnya buat riset pola konten yang jalan, bukan jualan angka estimasi yang ngawang.</p>
        <div class="hero-actions">
          <a class="button primary" href="/signup">Coba gratis dulu</a>
          <a class="button secondary" href="/payment">Lihat Paket</a>
        </div>
        <div class="hero-strip">
          <div class="mini-pill">Bahasanya sederhana</div>
          <div class="mini-pill">Cocok buat tim kecil sampai agency</div>
          <div class="mini-pill">Bisa langsung dipakai tanpa setup aneh-aneh</div>
        </div>
      </div>

      <div class="hero-ui glass">
        <div class="hero-topbar">
          <strong>Preview interaktif</strong>
          <div class="toggle-group">
            <button class="toggle active" type="button" data-demo="tren">Lagi rame</button>
            <button class="toggle" type="button" data-demo="creator">Vetting creator</button>
            <button class="toggle" type="button" data-demo="komentar">Baca komentar</button>
          </div>
        </div>
        <div class="showcase">
          <div class="search-shell">
            <input class="search-input" id="demo-query" value="Cari: skincare viral buat remaja" readonly>
            <div class="filter-row" id="demo-filters">
              <div class="chip">TikTok</div>
              <div class="chip">Instagram</div>
              <div class="chip">30 hari terakhir</div>
              <div class="chip">Min. 100 ribu views</div>
            </div>
            <div class="preview-stack">
              <div class="data-grid">
                <div class="results-column" id="demo-results">
                  <div class="result-card">
                    <strong>Hook “3 hari bikin wajah lebih kalem” naik cepat</strong>
                    <span>Video pendek edukasi + before after ringan paling sering muncul di hasil atas.</span>
                  </div>
                  <div class="result-card">
                    <strong>Format review jujur lebih disukai</strong>
                    <span>Komentar banyak membandingkan hasil asli, bukan video yang terlalu promosi.</span>
                  </div>
                </div>
                <div class="metric-column" id="demo-metrics">
                  <div class="metric-card highlight">
                    <strong>Trend score: 8.9/10</strong>
                    <span>Topik ini sedang ramai dan masih punya ruang untuk ikut masuk.</span>
                  </div>
                  <div class="metric-card">
                    <strong>Creator aktif: 27 akun</strong>
                    <span>Bisa langsung buka profil untuk cek performa dan pola postingan.</span>
                  </div>
                </div>
              </div>
              <div class="right-rail">
                <div class="rail-panel">
                  <h4>Panel cepat</h4>
                  <div class="rail-score" id="demo-rail-scores">
                    <div class="score-item"><strong>Hook kuat</strong><span>Kalimat pembuka yang bikin orang berhenti scroll.</span></div>
                    <div class="score-item"><strong>Komentar hidup</strong><span>Banyak pertanyaan dan respon nyata dari audiens.</span></div>
                  </div>
                </div>
                <div class="rail-panel">
                  <h4>Contoh feed</h4>
                  <div class="mini-feed" id="demo-mini-feed">
                    <div class="mini-video"><strong>“Kulit merah jadi lebih tenang?”</strong><span>2,4 jt views</span></div>
                    <div class="mini-video"><strong>“Skincare murah yang ternyata works”</strong><span>980 rb views</span></div>
                  </div>
                </div>
              </div>
            </div>
            <div class="stats-row" id="demo-stats">
              <div class="stat-box"><strong>124</strong><span>hasil kepilih</span></div>
              <div class="stat-box"><strong>2.4 jt</strong><span>rata-rata views</span></div>
              <div class="stat-box"><strong>18 mnt</strong><span>waktu yang dihemat</span></div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </header>

  <section id="nilai">
    <div class="container">
      <div class="section-head">
        <h2>Nggak banyak klik. Nggak banyak nebak.</h2>
        <p>Orang pakai tool kayak gini bukan karena suka angka. Orang pakai karena pengen cepat ngerti apa yang lagi jalan di pasar.</p>
      </div>
      <div class="quick-proof">
        <div class="proof-card"><strong>1 kotak cari</strong><span>Ketik seperti biasa, hasilnya langsung dirapikan.</span></div>
        <div class="proof-card"><strong>5 platform</strong><span>Nggak perlu pindah-pindah tab dari TikTok ke Instagram lalu balik lagi.</span></div>
        <div class="proof-card"><strong>Hook sampai komentar</strong><span>Bukan cuma link video, tapi konteksnya juga kebaca.</span></div>
      </div>
      <div class="value-grid">
        <div class="value-card big glass">
          <h3>Satu tempat buat kerja yang biasanya bikin browser penuh tab</h3>
          <p>Cari topik, cek profil, baca komentar, lihat sinyal promosi, dan nangkep isi video. Semuanya dibikin lebih enak dibaca, bukan mentah.</p>
          <div class="stack-list">
            <div class="stack-item">Cari kata kunci lintas TikTok, Instagram, YouTube, X, dan Facebook.</div>
            <div class="stack-item">Filter hasil pakai views, likes, tanggal, dan urutan yang masuk akal.</div>
            <div class="stack-item">Buka profil creator buat lihat rata-rata performa konten dengan cepat.</div>
          </div>
        </div>
        <div class="value-card glass">
          <h3>Lihat isi pasar dari komentar beneran</h3>
          <p>Cari keluhan, candaan, pujian, keberatan, dan kata-kata yang memang dipakai audiens sehari-hari.</p>
        </div>
        <div class="value-card glass">
          <h3>Pahami video tanpa harus nonton semuanya</h3>
          <p>Transkrip bantu screening cepat. Cocok buat riset konten, cari angle, dan shortlist creator.</p>
        </div>
      </div>
    </div>
  </section>

  <section id="pakai">
    <div class="container">
      <div class="section-head">
        <h2>Pakai sesuai cara kerja kamu</h2>
        <p>Biar nggak terasa kayak halaman brosur, saya bikin bagian ini lebih kebayang dipakai sehari-hari: buat owner, agency, atau tim yang lagi cari creator.</p>
      </div>
      <div class="mode-tabs">
        <button class="mode-tab active" type="button" data-mode="umkm">UMKM</button>
        <button class="mode-tab" type="button" data-mode="agency">Agency</button>
        <button class="mode-tab" type="button" data-mode="creator">Creator scout</button>
      </div>

      <div class="scenario-panel glass active" data-panel="umkm">
        <div class="scenario-copy">
          <h3>Cari ide konten dan tahu orang ngomong apa sebelum posting.</h3>
          <p>Buat owner atau admin, yang penting itu simpel: topik apa yang lagi hidup, angle apa yang dipakai kompetitor, dan komentar seperti apa yang paling sering muncul.</p>
          <div class="scenario-points">
            <div class="scenario-point">Cari topik seperti “kopi kekinian”, “serum jerawat”, atau “jualan frozen food”.</div>
            <div class="scenario-point">Lihat konten paling ramai dalam 7 atau 30 hari terakhir.</div>
            <div class="scenario-point">Ambil bahasa komentar untuk bahan caption, hook, atau penawaran.</div>
          </div>
        </div>
        <div class="scenario-shot">
          <div class="shot-header">
            <strong>Contoh hasil</strong>
            <span class="shot-label">Mode UMKM</span>
          </div>
          <div class="feed-grid">
            <div class="feed-card"><strong>Topik ramai</strong><span>“Serum barrier repair” naik karena banyak komentar soal iritasi ringan.</span></div>
            <div class="feed-card"><strong>Format menang</strong><span>Video singkat 20-30 detik dengan hook masalah nyata paling cepat naik.</span></div>
            <div class="feed-card"><strong>Komentar dominan</strong><span>Orang banyak tanya “buat kulit sensitif aman ga?” dan “berapa lama kelihatan hasilnya?”.</span></div>
            <div class="feed-card"><strong>Arah konten</strong><span>Bisa lanjut ke edukasi, testimoni, atau perbandingan sebelum-sesudah.</span></div>
          </div>
        </div>
      </div>

      <div class="scenario-panel glass" data-panel="agency">
        <div class="scenario-copy">
          <h3>Riset lebih cepat buat pitch, report, dan shortlist creator.</h3>
          <p>Buat tim agency, Sinyal kepakainya pas banget buat motong waktu buka tab satu-satu. Fokusnya: siapa yang layak dipantau, topik mana yang lagi naik, dan konten mana yang perform.</p>
          <div class="scenario-points">
            <div class="scenario-point">Bandingkan akun creator dari performa rata-rata, engagement, dan pola postingan.</div>
            <div class="scenario-point">Tarik komentar buat cari pain point dan angle campaign.</div>
            <div class="scenario-point">Filter hasil dengan views minimum dan tanggal biar report lebih bersih.</div>
          </div>
        </div>
        <div class="scenario-shot">
          <div class="shot-header">
            <strong>Contoh hasil</strong>
            <span class="shot-label">Mode Agency</span>
          </div>
          <div class="feed-grid">
            <div class="feed-card"><strong>Shortlist creator</strong><span>5 akun naik ke atas karena performa stabil dan komentar audiens aktif.</span></div>
            <div class="feed-card"><strong>Sinyal promosi</strong><span>Konten sponsor bisa dipisah dari konten organik untuk lihat performa asli.</span></div>
            <div class="feed-card"><strong>Ringkasan cepat</strong><span>Views, likes, komentar, dan transkrip langsung terbaca dalam satu alur.</span></div>
            <div class="feed-card"><strong>Waktu hemat</strong><span>Riset awal yang biasa makan 1-2 jam bisa dipotong jauh lebih cepat.</span></div>
          </div>
        </div>
      </div>

      <div class="scenario-panel glass" data-panel="creator">
        <div class="scenario-copy">
          <h3>Cari creator yang pas, bukan cuma yang followers-nya besar.</h3>
          <p>Kalau tugasnya sourcing creator, yang penting bukan cuma followers. Kamu perlu lihat komentar, gaya bahasa, rata-rata views, dan apakah kontennya masih natural atau kebanyakan promosi.</p>
          <div class="scenario-points">
            <div class="scenario-point">Buka profil creator dan lihat rata-rata performa postingan.</div>
            <div class="scenario-point">Baca komentar untuk cek kualitas interaksi audiens.</div>
            <div class="scenario-point">Pakai transkrip untuk screening cepat tanpa nonton semua video.</div>
          </div>
        </div>
        <div class="scenario-shot">
          <div class="shot-header">
            <strong>Contoh hasil</strong>
            <span class="shot-label">Mode Creator Scout</span>
          </div>
          <div class="feed-grid">
            <div class="feed-card"><strong>Engagement stabil</strong><span>Akun dengan followers sedang tapi komentar hidup sering lebih menarik.</span></div>
            <div class="feed-card"><strong>Tone cocok</strong><span>Bahasa video dan komentar lebih nyambung untuk brand lokal.</span></div>
            <div class="feed-card"><strong>Risiko sponsor</strong><span>Konten terlalu sering promosi bisa terlihat dari pola feed dan caption.</span></div>
            <div class="feed-card"><strong>Screening cepat</strong><span>Transkrip bantu saring banyak creator tanpa capek nonton satu per satu.</span></div>
          </div>
        </div>
      </div>
    </div>
  </section>

  <section id="harga">
    <div class="container pricing-wrap glass">
      <div class="section-head">
        <h2>Harganya dibuat biar masih masuk akal</h2>
        <p>Kita mulai dari harga yang masih bisa dicoba dulu, tapi tetap pakai batas pemakaian supaya service-nya tetap sehat saat user mulai banyak.</p>
      </div>
      <div class="pricing-grid">
        <div class="price-card">
          <div class="badge">Mulai hemat</div>
          <h3>Paket Ringan</h3>
          <div class="price">Rp59rb <small>/ bulan</small></div>
          <div class="price-note">Cocok buat coba rutin tanpa langsung keluar biaya besar.</div>
          <div class="price-list">
            <div>30 pencarian per bulan</div>
            <div>10 cek profil</div>
            <div>10 tarik komentar</div>
            <div>10 transkrip video</div>
          </div>
          <a class="price-cta" href="/checkout/ringan">Mulai Paket Ringan</a>
        </div>
        <div class="price-card featured">
          <div class="badge">Paling masuk akal</div>
          <h3>Paket Tumbuh</h3>
          <div class="price">Rp99rb <small>/ bulan</small></div>
          <div class="price-note">Pilihan paling aman buat pemakaian rutin tim kecil, brand, atau agency.</div>
          <div class="price-list">
            <div>120 pencarian per bulan</div>
            <div>40 cek profil</div>
            <div>40 tarik komentar</div>
            <div>40 transkrip video</div>
          </div>
          <a class="price-cta" href="/checkout/tumbuh">Ambil Paket Tumbuh</a>
        </div>
        <div class="price-card">
          <div class="badge">Untuk tim</div>
          <h3>Paket Tim</h3>
          <div class="price">Rp299rb <small>/ bulan</small></div>
          <div class="price-note">Kalau sudah dipakai beberapa orang dan butuh kuota lebih longgar.</div>
          <div class="price-list">
            <div>500 pencarian per bulan</div>
            <div>150 cek profil</div>
            <div>150 tarik komentar</div>
            <div>150 transkrip video</div>
            <div>3 anggota tim</div>
          </div>
          <a class="price-cta" href="/checkout/tim">Ambil Paket Tim</a>
        </div>
      </div>
    </div>
  </section>

  <section>
    <div class="container cta-panel glass">
      <div>
        <h2>Masuk, ketik topik, lalu lihat sendiri enaknya.</h2>
        <p>Tujuan landing page ini sekarang jelas: bikin orang cepat paham produknya, bukan capek baca. Dari sini tinggal daftar, pilih paket kalau perlu, lalu coba workflow aslinya.</p>
      </div>
      <a class="button primary" href="/signup">Coba sekarang</a>
    </div>
  </section>

  <footer>
    <div class="container">
      Sinyal membantu creator dan affiliate marketer Indonesia ngebongkar pola konten publik: hook, caption, komentar, dan transkrip, tanpa harus lompat-lompat antar platform.
    </div>
  </footer>
</div>

<script>
const heroDemos = {
  tren: {
    query: "Cari topik: skincare viral buat remaja",
    filters: ["TikTok", "Instagram", "30 hari terakhir", "Min. 100 ribu views"],
    results: [
      ["Hook “3 hari bikin wajah lebih kalem” lagi naik", "Video edukasi pendek + before after ringan paling sering nongol di hasil atas."],
      ["Review jujur lebih gampang nyangkut", "Komentar banyak ngebandingin hasil asli, bukan video yang terlalu jualan."]
    ],
    metrics: [
      ["Trend score: 8.9/10", "Topik ini lagi rame dan masih kebuka buat ikut masuk."],
      ["Creator aktif: 27 akun", "Bisa langsung dibuka satu-satu buat cek performa feed-nya."]
    ],
    rail: [
      ["Hook kuat", "Kalimat pembuka yang bikin orang berhenti scroll."],
      ["Komentar hidup", "Banyak pertanyaan dan respon nyata dari audiens."]
    ],
    feed: [
      ["“Kulit merah jadi lebih tenang?”", "2,4 jt views"],
      ["“Skincare murah yang ternyata works”", "980 rb views"]
    ],
    stats: [["124", "hasil kepilih"], ["2.4 jt", "rata-rata views"], ["18 mnt", "waktu yang dihemat"]]
  },
  creator: {
    query: "Cari creator: finansial Indonesia",
    filters: ["TikTok", "YouTube", "7 hari terakhir", "Sort: paling banyak views"],
    results: [
      ["Akun dengan komentar aktif langsung naik", "Bukan cuma views, tapi kualitas interaksinya juga kelihatan."],
      ["Konten sponsor bisa dipisah", "Jadi lebih gampang nilai performa organik sebelum shortlist creator."]
    ],
    metrics: [
      ["Engagement rata-rata: 7.4%", "Akun yang stabil lebih enak dipilih buat campaign yang butuh trust."],
      ["Shortlist cepat: 8 akun", "Profil bisa dicek tanpa pindah-pindah platform."]
    ],
    rail: [
      ["Organik vs sponsor", "Feed yang terlalu banyak promo gampang keliatan."],
      ["Komentar relevan", "Bisa cek apakah audiensnya beneran nyambung."]
    ],
    feed: [
      ["“Cara atur duit gajian biar nggak bocor”", "640 rb views"],
      ["“Investasi pemula jangan mulai dari sini”", "410 rb views"]
    ],
    stats: [["8", "akun shortlist"], ["7.4%", "engagement rata-rata"], ["3x", "lebih cepat screening"]]
  },
  komentar: {
    query: "Cari komentar: kopi susu literan",
    filters: ["TikTok", "Komentar", "30 hari terakhir", "Transkrip aktif"],
    results: [
      ["Komentar dominan: kemanisan dan harga", "Audiens paling sering bahas rasa terlalu manis dan porsi yang cocok buat sharing."],
      ["Caption dan komentar saling nyambung", "Enak buat cari angle promosi yang terasa natural, bukan maksa."]
    ],
    metrics: [
      ["Komentar kebaca: 52", "Bisa dipakai buat baca bahasa pasar yang asli dan pertanyaan yang berulang."],
      ["Transkrip siap baca", "Isi video cepat dipahami tanpa harus nonton semuanya."]
    ],
    rail: [
      ["Pain point ketemu", "Harga dan rasa jadi dua hal yang paling sering disebut."],
      ["Bahasa pasar kebaca", "Bisa langsung dipakai buat caption dan angle konten berikutnya."]
    ],
    feed: [
      ["“Kenapa gelasnya kecil tapi manis banget”", "Komentar paling sering muncul"],
      ["“Enak buat sharing, tapi harganya naik ya?”", "Komentar bernada beli ulang"]
    ],
    stats: [["52", "komentar terbaca"], ["11", "pain point muncul"], ["1 layar", "semua insight"]]
  }
};

const scenarioButtons = document.querySelectorAll(".mode-tab");
const scenarioPanels = document.querySelectorAll(".scenario-panel");
scenarioButtons.forEach((button) => {
  button.addEventListener("click", () => {
    scenarioButtons.forEach((item) => item.classList.remove("active"));
    scenarioPanels.forEach((panel) => panel.classList.remove("active"));
    button.classList.add("active");
    const panel = document.querySelector(`[data-panel="${button.dataset.mode}"]`);
    if (panel) {
      panel.classList.add("active");
    }
  });
});

const toggleButtons = document.querySelectorAll(".toggle");
const demoQuery = document.getElementById("demo-query");
const demoFilters = document.getElementById("demo-filters");
const demoResults = document.getElementById("demo-results");
const demoMetrics = document.getElementById("demo-metrics");
const demoStats = document.getElementById("demo-stats");
const demoRailScores = document.getElementById("demo-rail-scores");
const demoMiniFeed = document.getElementById("demo-mini-feed");

function renderDemo(name) {
  const demo = heroDemos[name];
  if (!demo) return;
  demoQuery.value = demo.query;
  demoFilters.innerHTML = demo.filters.map((item) => `<div class="chip">${item}</div>`).join("");
  demoResults.innerHTML = demo.results.map(([title, desc]) => `<div class="result-card"><strong>${title}</strong><span>${desc}</span></div>`).join("");
  demoMetrics.innerHTML = demo.metrics.map(([title, desc], index) => `<div class="metric-card${index === 0 ? " highlight" : ""}"><strong>${title}</strong><span>${desc}</span></div>`).join("");
  demoRailScores.innerHTML = demo.rail.map(([title, desc]) => `<div class="score-item"><strong>${title}</strong><span>${desc}</span></div>`).join("");
  demoMiniFeed.innerHTML = demo.feed.map(([title, meta]) => `<div class="mini-video"><strong>${title}</strong><span>${meta}</span></div>`).join("");
  demoStats.innerHTML = demo.stats.map(([value, label]) => `<div class="stat-box"><strong>${value}</strong><span>${label}</span></div>`).join("");
}

toggleButtons.forEach((button) => {
  button.addEventListener("click", () => {
    toggleButtons.forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    renderDemo(button.dataset.demo);
  });
});

renderDemo("tren");
</script>
</body>
</html>"""


APP_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta content="width=device-width, initial-scale=1.0" name="viewport"/>
<title>Sinyal | Intelligence Terminal</title>
<script>
  if (localStorage.theme === 'dark' || (!('theme' in localStorage) && window.matchMedia('(prefers-color-scheme: dark)').matches)) {
    document.documentElement.classList.add('dark');
  }
</script>
<script src="https://cdn.tailwindcss.com?plugins=forms,container-queries"></script>
<link href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700;800&family=Inter:wght@400;500;600&display=swap" rel="stylesheet"/>
<link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&display=swap" rel="stylesheet"/>
<script id="tailwind-config">
    tailwind.config = {
        darkMode: "class",
        theme: {
            extend: {
                colors: {
                    "background":             "rgb(var(--c-bg) / <alpha-value>)",
                    "surface":                "rgb(var(--c-bg) / <alpha-value>)",
                    "surface-dim":            "rgb(var(--c-surface-dim) / <alpha-value>)",
                    "sidebar":                "rgb(var(--c-sidebar) / <alpha-value>)",
                    "surface-container-low":  "rgb(var(--c-scl) / <alpha-value>)",
                    "surface-container":      "rgb(var(--c-sc) / <alpha-value>)",
                    "surface-container-high": "rgb(var(--c-sch) / <alpha-value>)",
                    "surface-container-highest":"rgb(var(--c-schh) / <alpha-value>)",
                    "surface-container-lowest":"rgb(var(--c-sclo) / <alpha-value>)",
                    "surface-variant":        "rgb(var(--c-sv) / <alpha-value>)",
                    "on-surface":             "rgb(var(--c-on) / <alpha-value>)",
                    "on-surface-variant":     "rgb(var(--c-onv) / <alpha-value>)",
                    "primary":                "rgb(var(--c-pri) / <alpha-value>)",
                    "primary-container":      "rgb(var(--c-pric) / <alpha-value>)",
                    "on-primary-fixed":       "rgb(var(--c-opf) / <alpha-value>)",
                    "on-primary-fixed-variant":"rgb(var(--c-opf) / <alpha-value>)",
                    "on-primary-container":   "rgb(var(--c-opc) / <alpha-value>)",
                    "outline-variant":        "rgb(var(--c-ov) / <alpha-value>)",
                    "error":                  "rgb(var(--c-err) / <alpha-value>)",
                    "on-error-container":     "rgb(var(--c-oec) / <alpha-value>)",
                    "brand":                  "rgb(var(--c-brand) / <alpha-value>)",
                    "tab-text":               "rgb(var(--c-tab) / <alpha-value>)",
                },
                fontFamily: {
                    "headline": ["Manrope"],
                    "body": ["Inter"],
                    "label": ["Inter"]
                },
                borderRadius: {"DEFAULT": "0.25rem", "lg": "0.5rem", "xl": "0.75rem", "full": "9999px"},
            },
        },
    }
</script>
<style>
    /* ===== LIGHT MODE (default) ===== */
    :root {
        --c-bg: 252 249 244;
        --c-sidebar: 255 255 255;
        --c-surface-dim: 238 234 226;
        --c-scl: 246 242 236;
        --c-sc: 240 236 228;
        --c-sch: 232 227 219;
        --c-schh: 224 219 211;
        --c-sclo: 255 255 255;
        --c-sv: 232 227 219;
        --c-on: 28 26 22;
        --c-onv: 107 94 82;
        --c-pri: 160 78 0;
        --c-pric: 230 126 34;
        --c-opf: 255 255 255;
        --c-opc: 110 52 0;
        --c-ov: 212 198 184;
        --c-err: 186 26 26;
        --c-oec: 65 0 2;
        --c-brand: 210 105 15;
        --c-tab: 135 124 114;
        --scrollbar-track: #f4f0ea;
        --scrollbar-thumb: #d4c6b8;
        --input-border: rgba(180,160,140,0.25);
        --active-tab-bg: rgba(230,126,34,0.07);
        --sidebar-shadow: 2px 0 8px rgba(0,0,0,0.04);
        --card-shadow: 0 1px 3px rgba(0,0,0,0.06);
        --focus-ring: rgba(160,78,0,0.4);
    }
    /* ===== DARK MODE ===== */
    .dark {
        --c-bg: 17 19 24;
        --c-sidebar: 12 14 18;
        --c-surface-dim: 17 19 24;
        --c-scl: 26 28 32;
        --c-sc: 30 32 36;
        --c-sch: 40 42 46;
        --c-schh: 51 53 57;
        --c-sclo: 12 14 18;
        --c-sv: 51 53 57;
        --c-on: 226 226 232;
        --c-onv: 220 193 177;
        --c-pri: 255 183 131;
        --c-pric: 230 126 34;
        --c-opf: 113 55 0;
        --c-opc: 80 38 0;
        --c-ov: 86 67 55;
        --c-err: 255 180 171;
        --c-oec: 255 218 214;
        --c-brand: 255 183 131;
        --c-tab: 148 148 148;
        --scrollbar-track: #0c0e12;
        --scrollbar-thumb: #333539;
        --input-border: rgba(86,67,55,0.2);
        --active-tab-bg: rgba(230,126,34,0.1);
        --sidebar-shadow: 2px 0 12px rgba(0,0,0,0.3);
        --card-shadow: none;
        --focus-ring: rgba(255,183,131,0.5);
    }
    .material-symbols-outlined {
        font-variation-settings: 'FILL' 0, 'wght' 400, 'GRAD' 0, 'opsz' 24;
        vertical-align: middle;
    }
    body {
        background-color: rgb(var(--c-bg));
        color: rgb(var(--c-on));
        font-family: 'Inter', sans-serif;
        transition: background-color 0.35s ease, color 0.35s ease;
    }
    .custom-scrollbar::-webkit-scrollbar { width: 4px; }
    .custom-scrollbar::-webkit-scrollbar-track { background: var(--scrollbar-track); }
    .custom-scrollbar::-webkit-scrollbar-thumb { background: var(--scrollbar-thumb); border-radius: 10px; }
    .tab-btn.active {
        background-color: var(--active-tab-bg);
        color: rgb(var(--c-pri));
        border-right-width: 4px;
        border-color: rgb(var(--c-pric));
    }
    .ds-input {
        background-color: rgb(var(--c-sclo));
        border: 1px solid var(--input-border);
        color: rgb(var(--c-on));
        border-radius: 0.75rem;
        font-size: 0.875rem;
        transition: background-color 0.3s, border-color 0.3s, color 0.3s;
    }
    .ds-input:focus {
        outline: none;
        border-color: var(--focus-ring);
        box-shadow: 0 0 0 1px var(--focus-ring);
    }
    .theme-transition, aside, main, header, .tab-btn, .ds-input {
        transition: background-color 0.35s ease, color 0.35s ease, border-color 0.35s ease, box-shadow 0.35s ease;
    }
</style>
</head>
<body class="bg-background text-on-surface custom-scrollbar">

<div class="flex h-screen overflow-hidden">
    <!-- SideNavBar -->
    <aside class="fixed left-0 top-0 h-full flex flex-col p-4 z-40 bg-sidebar w-64 transition-all border-r border-outline-variant/10" style="box-shadow: var(--sidebar-shadow);">
        <div class="mb-8 px-4 flex items-center gap-3">
            <div class="w-10 h-10 bg-primary-container rounded-lg flex items-center justify-center">
                <span class="material-symbols-outlined text-on-primary-fixed" data-icon="insights">insights</span>
            </div>
            <div>
                <h1 class="text-xl font-extrabold text-brand font-headline tracking-tight">Sinyal</h1>
                <p class="text-[10px] uppercase tracking-widest text-on-surface-variant font-bold">Editorial Intel</p>
            </div>
        </div>
        <nav class="flex-1 space-y-1">
            <button class="tab-btn active w-full flex items-center gap-3 text-tab-text rounded-xl px-4 py-3 font-manrope font-semibold text-sm transition-all hover:bg-surface-container-low hover:text-primary" onclick="switchTab('dashboard', this)">
                <span class="material-symbols-outlined" data-icon="insights">insights</span>
                <span>Intelligence</span>
            </button>
            <button class="tab-btn w-full flex items-center gap-3 text-tab-text px-4 py-3 rounded-xl border-r-4 border-transparent font-manrope font-semibold text-sm hover:bg-surface-container-low hover:text-primary transition-all" onclick="switchTab('search', this)">
                <span class="material-symbols-outlined" data-icon="search">search</span>
                <span>Riset</span>
            </button>
            <button class="tab-btn w-full flex items-center gap-3 text-tab-text px-4 py-3 rounded-xl border-r-4 border-transparent font-manrope font-semibold text-sm hover:bg-surface-container-low hover:text-primary transition-all" onclick="switchTab('profile', this)">
                <span class="material-symbols-outlined" data-icon="movie_filter">movie_filter</span>
                <span>Profil</span>
            </button>
            <button class="tab-btn w-full flex items-center gap-3 text-tab-text px-4 py-3 rounded-xl border-r-4 border-transparent font-manrope font-semibold text-sm hover:bg-surface-container-low hover:text-primary transition-all" onclick="switchTab('comments', this)">
                <span class="material-symbols-outlined" data-icon="forum">forum</span>
                <span>Komentar</span>
            </button>
            <button class="w-full flex items-center gap-3 text-tab-text px-4 py-3 font-manrope font-semibold text-sm hover:bg-surface-container-low hover:text-primary transition-all mt-auto" onclick="window.location.href='/payment'">
                <span class="material-symbols-outlined" data-icon="settings">settings</span>
                <span>Billing</span>
            </button>
        </nav>
        <div class="mt-8 p-4 bg-surface-container-high rounded-xl border border-outline-variant/20">
            <p class="text-xs text-on-surface-variant mb-3">Professional analytics for the top 1% of creators.</p>
            <button class="w-full py-2.5 bg-gradient-to-br from-primary to-primary-container text-on-primary-fixed font-bold text-sm rounded-lg hover:shadow-[0_0_15px_rgba(230,126,34,0.3)] transition-all">
                Upgrade to Pro
            </button>
        </div>
    </aside>

    <!-- Main Terminal Canvas -->
    <main class="ml-64 flex-1 flex flex-col min-w-0 bg-surface-dim">
        <header class="flex justify-between items-center w-full px-8 h-20 sticky top-0 z-50 bg-background border-b border-outline-variant/10">
            <div class="flex items-center gap-6 flex-1 max-w-2xl">
                <div class="relative w-full">
                    <span class="material-symbols-outlined absolute left-4 top-1/2 -translate-y-1/2 text-on-surface-variant">search</span>
                    <input id="globalSearch" class="ds-input w-full py-3 pl-12 pr-4" placeholder="Quick find creators or hooks..." type="text"/>
                </div>
            </div>
            <div class="flex items-center gap-4">
                <div class="flex items-center gap-1 bg-surface-container-low p-1 rounded-full">
                    <button onclick="toggleTheme()" class="p-2 text-on-surface-variant hover:text-primary transition-colors" title="Toggle theme">
                        <span id="themeIcon" class="material-symbols-outlined">dark_mode</span>
                    </button>
                    <button class="p-2 text-on-surface-variant hover:text-primary transition-colors"><span class="material-symbols-outlined">notifications</span></button>
                    <button class="p-2 text-on-surface-variant hover:text-primary transition-colors"><span class="material-symbols-outlined">help_outline</span></button>
                </div>
                <div class="flex h-9 w-9 items-center justify-center rounded-full bg-primary/15 font-bold text-primary border border-primary/30">A</div>
            </div>
        </header>

        <div class="flex-1 overflow-y-auto p-8 custom-scrollbar">
            <!-- DASHBOARD TAB -->
            <section id="dashboardTab" class="tab-section">
                <section class="mb-10">
                    <div class="flex justify-between items-end mb-6">
                        <div>
                            <span class="text-primary font-bold text-xs tracking-widest uppercase mb-2 block">Global Signal Map</span>
                            <h2 class="text-4xl font-black font-headline tracking-tighter text-on-surface">Intelligence Dashboard</h2>
                        </div>
                        <div class="flex items-center gap-3">
                            <div class="text-right">
                                <p class="text-[10px] font-bold text-on-surface-variant uppercase">Market Sentiment</p>
                                <p class="text-sm font-headline font-bold text-primary">Bullish +14.2%</p>
                            </div>
                            <div class="h-10 w-[2px] bg-outline-variant/30"></div>
                            <div class="text-right">
                                <p class="text-[10px] font-bold text-on-surface-variant uppercase">Active Signals</p>
                                <p class="text-sm font-headline font-bold text-on-surface">1,402</p>
                            </div>
                        </div>
                    </div>
                    <!-- Bento Terminal Grid -->
                    <div class="grid grid-cols-12 gap-4">
                        <div class="col-span-8 bg-surface-container-low rounded-xl p-6 border border-outline-variant/10 relative overflow-hidden h-[400px]" style="box-shadow: var(--card-shadow);">
                            <div class="flex justify-between items-start mb-4 relative z-10">
                                <div>
                                    <h3 class="text-lg font-bold font-headline">Viral Velocity Tracking</h3>
                                    <p class="text-xs text-on-surface-variant">Real-time content performance across platforms</p>
                                </div>
                                <div class="flex gap-2">
                                    <span class="px-2 py-1 bg-primary/10 text-primary text-[10px] font-bold rounded">LIVE</span>
                                </div>
                            </div>
                            <div class="absolute inset-0 top-20 flex items-end opacity-40">
                                <div class="w-full h-full bg-gradient-to-t from-primary/20 to-transparent"></div>
                            </div>
                            <div class="absolute inset-0 top-24 flex items-center justify-center">
                                <img class="w-full h-full object-cover mix-blend-overlay opacity-30" src="https://lh3.googleusercontent.com/aida-public/AB6AXuACchwIkLsq0cX0oYFtexG3ezyeZbFOOfI548UVNEYVTvQsayUaU9LyeFC-ja8SCXA2itl0pF_xUV2lKoZIyVLGkZWZGmUz3J7jTj_-HLaBF4MQGUFO20Tr37RNd2s2X-ovs_KH1EAOY7ivsEEpleObCOthVgyQu3vvSzX3sUxxl735WgdBjrXRvz7mF-7L2UXdVGwbewz4m81k1Uyqn8JGMzjf_XboDuaw7tPaKmB5FmFY7Y3wffA2x9zIqw8dDe9sdL0jO0IVHg"/>
                                <div class="absolute bottom-10 left-10 right-10 flex justify-between">
                                    <div class="space-y-1">
                                        <p class="text-[10px] text-on-surface-variant font-bold uppercase">Peak Saturation</p>
                                        <p class="text-2xl font-bold font-headline text-primary">89.4%</p>
                                    </div>
                                    <div class="text-right space-y-1">
                                        <p class="text-[10px] text-on-surface-variant font-bold uppercase">Decay Start</p>
                                        <p class="text-2xl font-bold font-headline text-on-surface">Oct 24, 2026</p>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div class="col-span-4 space-y-4">
                            <div class="bg-surface-container-high rounded-xl p-5 border border-outline-variant/10" style="box-shadow: var(--card-shadow);">
                                <div class="flex items-center gap-2 mb-4">
                                    <span class="material-symbols-outlined text-primary" style="font-variation-settings: 'FILL' 1;">local_fire_department</span>
                                    <h4 class="text-sm font-bold uppercase tracking-tight">Emerging Hooks</h4>
                                </div>
                                <ul class="space-y-3">
                                    <li class="flex justify-between items-center group cursor-pointer">
                                        <span class="text-xs text-on-surface group-hover:text-primary transition-colors">"I didn't think I'd..."</span>
                                        <span class="text-[10px] bg-surface-container-highest px-2 py-0.5 rounded text-on-surface-variant">+240%</span>
                                    </li>
                                    <li class="flex justify-between items-center group cursor-pointer">
                                        <span class="text-xs text-on-surface group-hover:text-primary transition-colors">Lofi Productivity Hacks</span>
                                        <span class="text-[10px] bg-surface-container-highest px-2 py-0.5 rounded text-on-surface-variant">+118%</span>
                                    </li>
                                    <li class="flex justify-between items-center group cursor-pointer">
                                        <span class="text-xs text-on-surface group-hover:text-primary transition-colors">Extreme Minimalist Vlogs</span>
                                        <span class="text-[10px] bg-surface-container-highest px-2 py-0.5 rounded text-on-surface-variant">+94%</span>
                                    </li>
                                </ul>
                            </div>
                            <div class="bg-surface-container-lowest rounded-xl p-5 border border-outline-variant/10 h-[212px] relative overflow-hidden group" style="box-shadow: var(--card-shadow);">
                                <img class="absolute inset-0 w-full h-full object-cover opacity-20 grayscale group-hover:grayscale-0 transition-all duration-700" src="https://lh3.googleusercontent.com/aida-public/AB6AXuDSGXq8T1indWOeXynsfBmXbe4xILe04-bTFTPeQ3HNaIQV0T4dc--aOR9pVySZ3Mhw93aX2kW9jLhKJ3y2B_EqNCLO_5mJtUMtgKys73piNC4ylyEGavx-fyIh1u7DFtLyCSiHTFBjKSA--Id7MjQUcD_QxJJzY2Z1ngr4gH0UlO1sHMnk1lE7VqDENGhgs3Xmay5b_4Ptd7nwGvKLBZ0ZiwGpX5D6vk5sObWJaZXcRES-lJI44j1oroMmKfE8-D0GztZqqVl5lA"/>
                                <div class="relative z-10">
                                    <h4 class="text-sm font-bold uppercase tracking-tight mb-1">Global Reach</h4>
                                    <p class="text-[10px] text-on-surface-variant">Top region: SEA (Indonesia)</p>
                                </div>
                                <div class="absolute bottom-4 right-4 bg-primary/20 backdrop-blur-md px-3 py-1 rounded-full border border-primary/30">
                                    <span class="text-[10px] font-bold text-primary">MAP VIEW</span>
                                </div>
                            </div>
                        </div>
                    </div>
                </section>
                <section>
                    <div class="flex items-center justify-between mb-4 border-b border-outline-variant/10 pb-4">
                        <div class="flex gap-6">
                            <button class="text-sm font-bold text-primary border-b-2 border-primary pb-4 -mb-[17px]">Feed Signals</button>
                            <button class="text-sm font-bold text-on-surface-variant hover:text-on-surface transition-colors pb-4">Watchlist</button>
                            <button class="text-sm font-bold text-on-surface-variant hover:text-on-surface transition-colors pb-4">Anomalies</button>
                        </div>
                    </div>
                    <div class="bg-surface-container-low rounded-xl p-6 border border-outline-variant/10" style="box-shadow: var(--card-shadow);">
                        <p class="text-sm text-on-surface-variant">Gunakan navigasi "Riset" atau "Profil" di panel kiri untuk mulai memancing intelijen real-time dan menarik data konten viral.</p>
                    </div>
                </section>
            </section>

            <!-- SEARCH TAB -->
            <section id="searchTab" class="tab-section hidden">
                <div class="bg-surface-container-low rounded-xl p-6 border border-outline-variant/10" style="box-shadow: var(--card-shadow);">
                    <h3 class="font-headline text-xl font-bold">Search Workspace (Intelligence Riset)</h3>
                    <p class="mt-2 text-sm text-on-surface-variant">Cari keyword lalu analisis sinyal konten langsung di terminal.</p>
                    <div class="mt-6 grid gap-4 lg:grid-cols-4">
                        <textarea id="keywordInput" class="ds-input lg:col-span-2 p-3 min-h-[120px]" placeholder="Masukkan keyword...">openai</textarea>
                        <select id="platformSelect" class="ds-input p-3">
                            <option value="tiktok">TikTok</option>
                            <option value="youtube">YouTube</option>
                            <option value="instagram">Instagram</option>
                            <option value="twitter">X</option>
                            <option value="facebook">Facebook</option>
                        </select>
                        <select id="sortBy" class="ds-input p-3">
                            <option value="relevance">Paling relevan</option>
                            <option value="popular">Views tertinggi</option>
                            <option value="most_liked">Likes tertinggi</option>
                            <option value="latest">Terbaru</option>
                        </select>
                        <select id="dateRange" class="ds-input p-3">
                            <option value="all">Sepanjang waktu</option>
                            <option value="7d">7 hari terakhir</option>
                            <option value="30d">30 hari terakhir</option>
                        </select>
                        <input id="minViews" type="number" class="ds-input p-3" placeholder="Min views" />
                        <input id="maxViews" type="number" class="ds-input p-3" placeholder="Max views" />
                        <input id="minLikes" type="number" class="ds-input p-3" placeholder="Min likes" />
                        <input id="maxLikes" type="number" class="ds-input p-3" placeholder="Max likes" />
                    </div>
                    <div class="mt-6 flex flex-wrap gap-3">
                        <button id="searchBtn" class="bg-gradient-to-br from-primary to-primary-container text-on-primary-fixed rounded-xl px-5 py-3 text-sm font-bold hover:shadow-[0_0_15px_rgba(230,126,34,0.3)] transition-all">Scan Sinyal</button>
                        <a id="jsonDownload" class="hidden rounded-xl bg-surface-container-high border border-outline-variant/20 px-5 py-3 text-sm font-bold text-on-surface hover:bg-surface-container-highest transition-all" href="#">Export JSON</a>
                        <a id="csvDownload" class="hidden rounded-xl bg-surface-container-high border border-outline-variant/20 px-5 py-3 text-sm font-bold text-on-surface hover:bg-surface-container-highest transition-all" href="#">Export CSV</a>
                    </div>
                    <p id="searchMeta" class="mt-4 text-sm font-mono text-primary/80"></p>
                    <div class="mt-8">
                        <div id="searchResults" class="text-sm"></div>
                    </div>
                </div>
            </section>

            <!-- PROFILE TAB -->
            <section id="profileTab" class="tab-section hidden">
                <div class="grid grid-cols-1 gap-6 xl:grid-cols-[1fr_320px]">
                    <div class="bg-surface-container-low rounded-xl p-6 border border-outline-variant/10" style="box-shadow: var(--card-shadow);">
                        <h3 class="font-headline text-xl font-bold">Profil Surveillance</h3>
                        <p class="mt-2 text-sm text-on-surface-variant">Analisis pola konten spesifik author TikTok.</p>
                        <div class="mt-6 flex flex-wrap gap-3">
                            <input id="profileInput" class="ds-input p-3 flex-1" placeholder="Masukkan username..." value="openai" />
                            <select id="profileSort" class="ds-input p-3"><option value="latest">Terbaru</option><option value="popular">Popular</option></select>
                            <select id="profileDateRange" class="ds-input p-3"><option value="all">Sepanjang waktu</option><option value="7d">7 hr terakhir</option></select>
                            <button id="profileLoadBtn" class="bg-gradient-to-br from-primary to-primary-container text-on-primary-fixed rounded-xl px-5 py-3 text-sm font-bold hover:shadow-[0_0_15px_rgba(230,126,34,0.3)] transition-all">Muat Profil</button>
                        </div>
                        <input id="profileFeedSearch" class="ds-input mt-4 w-full p-3" placeholder="Filter di dalam feed profil ini..." />
                        <div class="mt-8">
                            <div id="profileResults" class="divide-y divide-outline-variant/10 text-sm"></div>
                        </div>
                    </div>
                    <div id="profileAnalytics" class="bg-surface-container-high rounded-xl p-6 border border-outline-variant/10 text-sm text-on-surface-variant flex flex-col justify-center text-center" style="box-shadow: var(--card-shadow);">
                        <span class="material-symbols-outlined text-4xl mb-4 text-outline-variant" style="font-variation-settings:'wght' 200;">monitoring</span>
                        Belum ada data profil.
                    </div>
                </div>
            </section>

            <!-- COMMENTS TAB -->
            <section id="commentsTab" class="tab-section hidden">
                <div class="grid grid-cols-1 gap-6 xl:grid-cols-[1fr_320px]">
                    <div class="bg-surface-container-low rounded-xl p-6 border border-outline-variant/10" style="box-shadow: var(--card-shadow);">
                        <h3 class="font-headline text-xl font-bold">Komentar Intel</h3>
                        <p class="mt-2 text-sm text-on-surface-variant">Ambil komentar dari video TikTok untuk menemukan CTA dan respon audiens.</p>
                        <div class="mt-6 grid gap-3 lg:grid-cols-[1fr_120px_160px]">
                            <input id="commentsUrl" class="ds-input p-3" value="https://www.tiktok.com/@openai/video/7604654293966146829" />
                            <input id="commentsMax" type="number" class="ds-input p-3" value="5" />
                            <button id="commentsLoadBtn" class="bg-gradient-to-br from-primary to-primary-container text-on-primary-fixed rounded-xl px-5 py-3 text-sm font-bold hover:shadow-[0_0_15px_rgba(230,126,34,0.3)] transition-all">Ekstrak</button>
                        </div>
                        <p id="commentsMeta" class="mt-4 text-sm font-mono text-primary/80"></p>
                        <div id="commentsResults" class="mt-6 grid gap-3"></div>
                    </div>
                    <div class="bg-surface-container-high rounded-xl p-6 border border-outline-variant/10" style="box-shadow: var(--card-shadow);">
                        <h4 class="font-headline text-lg font-bold text-primary">Comment Intelligence Log</h4>
                        <div class="mt-4 font-mono text-[9px] text-on-surface-variant space-y-2 bg-surface-container-lowest p-3 rounded border border-outline-variant/10">
                            <p>&gt; Menunggu kueri URL...</p>
                            <p>&gt; Parsing komentar dapat memakan waktu, status akan muncul disini.</p>
                            <p class="animate-pulse">_</p>
                        </div>
                    </div>
                </div>
            </section>
        </div>
    </main>

    <!-- Right Side Terminal Panel -->
    <aside class="hidden xl:flex w-80 bg-sidebar border-l border-outline-variant/10 p-6 flex flex-col gap-6 overflow-y-auto custom-scrollbar" style="box-shadow: var(--sidebar-shadow);">
        <div class="space-y-4">
            <div class="flex justify-between items-center">
                <h3 class="text-xs font-black uppercase tracking-widest text-on-surface-variant">Intel Summary</h3>
                <span class="text-[10px] font-bold text-primary">AUTO-REFRESH: ON</span>
            </div>
            <div class="p-4 bg-surface-container-low rounded-xl border-l-4 border-primary" style="box-shadow: var(--card-shadow);">
                <p class="text-xs font-bold mb-1 text-on-surface">Critical Divergence</p>
                <p class="text-[11px] text-on-surface-variant leading-relaxed">
                    TikTok engagement for <span class="text-on-surface font-bold">#FinanceTok</span> has dropped 22% in the last 4 hours. Market is shifting toward "Long-form authenticity."
                </p>
            </div>
        </div>
        <div class="space-y-4">
            <h3 class="text-xs font-black uppercase tracking-widest text-on-surface-variant">Top Performers</h3>
            <div class="flex items-center gap-3 p-3 bg-surface-container-high rounded-lg hover:bg-surface-container-highest transition-colors cursor-pointer group">
                <img class="w-10 h-10 rounded-full object-cover grayscale group-hover:grayscale-0 transition-all" src="https://lh3.googleusercontent.com/aida-public/AB6AXuAX8lcs-cmEbZYFsCyZh-NtHPOTePlFHRVjhKGEWLSq-bCGyKrMZFeYuk8MwmRrRMCny6MHWJx-kpOFdrKXaZj2hjW3fmdCMB08FYPvgzlYeGRD3q35OT35zwnO7KDvalOAP_l-9_RAnIYuX3RIwiSSPqkno7EKsiQkQ_Px_kUXlBXy_j8I2-aRva9VlwSF-Ly994P0v5axqx3KiewsQGclaNDv-mnGFKScLwbMBA2xV1R7qBgVLjD3GBaK4MKsxoJU7km7kBYYvw" />
                <div>
                    <p class="text-xs font-bold text-on-surface">Alex Volkov</p>
                    <p class="text-[10px] text-on-surface-variant">Tech Insight &bull; 2.4M Subs</p>
                </div>
                <span class="material-symbols-outlined ml-auto text-primary text-sm">trending_up</span>
            </div>
            <div class="flex items-center gap-3 p-3 bg-surface-container-high rounded-lg hover:bg-surface-container-highest transition-colors cursor-pointer group">
                <img class="w-10 h-10 rounded-full object-cover grayscale group-hover:grayscale-0 transition-all" src="https://lh3.googleusercontent.com/aida-public/AB6AXuB-nDLRV8bp-PWwBGOvkcGna8RoZAoXg76refLz_Oogvbm5gBNAvrJHKmt72GIENQ-mwrx-BWsTYooP67nhJreg4XJCQB-SnTUUrTp6UgwDWSevQHIh6TOb0A7mtzKWp2cQL2fbWjJ72jLbKaXG97hd8WOrXZeYbQXleFGdObIadoSVdWYDIPTfGzjkzvJoVnL-Xo9CpTyChJgZQc6r_kFLKvKakAky0qnz1kDsXm5dk3DFwDn4dkQrxAvjPYG-foYJsanqKwjhyg" />
                <div>
                    <p class="text-xs font-bold text-on-surface">Elena Thorne</p>
                    <p class="text-[10px] text-on-surface-variant">Lifestyle &bull; 890k Subs</p>
                </div>
                <span class="material-symbols-outlined ml-auto text-primary text-sm">trending_up</span>
            </div>
        </div>
        <div class="mt-auto pt-6 border-t border-outline-variant/10">
            <div class="flex justify-between items-center mb-4">
                <h3 class="text-xs font-black uppercase tracking-widest text-on-surface-variant">Terminal Log</h3>
            </div>
            <div class="font-mono text-[9px] text-on-surface-variant space-y-1 bg-surface-container-lowest p-3 rounded border border-outline-variant/10">
                <p style="color: rgb(var(--c-green));">&gt; Syncing with API...</p>
                <p>&gt; Analysis engine online.</p>
                <p class="text-primary/70">&gt; Ready for hooks extraction.</p>
                <p class="animate-pulse">_</p>
            </div>
        </div>
    </aside>
</div>

<script>
    /* ===== THEME TOGGLE ===== */
    function updateThemeIcon() {
        const icon = document.getElementById('themeIcon');
        if (!icon) return;
        icon.textContent = document.documentElement.classList.contains('dark') ? 'light_mode' : 'dark_mode';
    }
    function toggleTheme() {
        document.documentElement.classList.toggle('dark');
        localStorage.theme = document.documentElement.classList.contains('dark') ? 'dark' : 'light';
        updateThemeIcon();
    }
    updateThemeIcon();

    /* ===== TAB SWITCHING ===== */
    function switchTab(name, el){
      document.querySelectorAll('.tab-btn').forEach(b=>{
          b.classList.remove('active');
          if(b.classList.contains('border-r-4')){
              b.classList.add('border-transparent');
          }
      });
      if (el) {
          el.classList.add('active');
          el.classList.remove('border-transparent');
      }
      
      const sections = {
        dashboard: document.getElementById('dashboardTab'),
        search: document.getElementById('searchTab'),
        profile: document.getElementById('profileTab'),
        comments: document.getElementById('commentsTab')
      };
      
      Object.entries(sections).forEach(([k,v])=> {
          if (v) {
              if (k === name) {
                  v.classList.remove('hidden');
                  v.style.display = 'block';
              } else {
                  v.classList.add('hidden');
                  v.style.display = 'none';
              }
          }
      });
    }

    /* ===== XSS PROTECTION ===== */
    function escapeHTML(str) {
      if (!str) return '';
      return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }

    /* ===== RESULT ROW ===== */
    function formatNum(n) {
      if (n == null) return '-';
      n = Number(n);
      if (n >= 1_000_000) return (n/1_000_000).toFixed(1) + 'M';
      if (n >= 1_000) return (n/1_000).toFixed(1) + 'K';
      return n.toString();
    }

    function copyHook(text) {
      navigator.clipboard.writeText(text).then(() => {
        // Brief visual feedback handled inline
      });
    }

    function rowResult(item) {
      const hook = escapeHTML(item.hook || item.title || item.caption || 'Tanpa judul');
      const rawHook = escapeHTML(item.hook || item.title || item.caption || 'Tanpa judul');
      const caption = escapeHTML(item.caption || item.content || item.description || '');
      const transcript = item.transcript ? escapeHTML(item.transcript.substring(0, 120)) : '';
      const hashtags = (item.hashtags || []).slice(0, 5);
      const music = escapeHTML(item.music || '');

      return `<div class="bg-surface-container-low rounded-xl p-5 border border-outline-variant/10 hover:border-primary/20 transition-all group mb-3" style="box-shadow: var(--card-shadow);">
        <div class="flex justify-between items-start gap-3 mb-3">
            <div class="flex-1 min-w-0">
                <div class="flex items-center gap-2 mb-1">
                    <span class="px-2 py-0.5 rounded text-[10px] font-bold bg-primary/10 text-primary uppercase">${escapeHTML(item.platform || 'N/A')}</span>
                    ${music ? '<span class="text-[10px] text-on-surface-variant flex items-center gap-1"><span class="material-symbols-outlined text-xs">music_note</span>' + music + '</span>' : ''}
                </div>
                <h5 class="text-sm font-bold text-on-surface group-hover:text-primary transition-colors leading-snug">${hook}</h5>
            </div>
            <button onclick="copyHook(this.dataset.hook)" data-hook="${rawHook}" class="shrink-0 flex items-center gap-1 px-2.5 py-1.5 bg-primary/10 hover:bg-primary/20 text-primary rounded-lg text-[10px] font-bold transition-all" title="Copy hook">
                <span class="material-symbols-outlined text-sm">content_copy</span> Copy
            </button>
        </div>
        ${caption ? '<p class="text-xs text-on-surface-variant leading-relaxed mb-3 line-clamp-2">' + caption + '</p>' : ''}
        ${transcript ? '<div class="bg-surface-container-highest rounded-lg px-3 py-2 mb-3 border-l-2 border-primary/30"><p class="text-[11px] text-on-surface-variant italic"><span class="text-primary font-bold text-[10px] uppercase mr-1">Transcript</span>' + transcript + '...</p></div>' : ''}
        <div class="flex items-center justify-between">
            <div class="flex gap-4">
                <span class="text-[10px] text-on-surface-variant flex items-center gap-1"><span class="material-symbols-outlined text-sm">visibility</span><strong class="text-on-surface">${formatNum(item.views)}</strong></span>
                <span class="text-[10px] text-on-surface-variant flex items-center gap-1"><span class="material-symbols-outlined text-sm">favorite</span><strong class="text-on-surface">${formatNum(item.likes)}</strong></span>
                <span class="text-[10px] text-on-surface-variant flex items-center gap-1"><span class="material-symbols-outlined text-sm">chat_bubble</span><strong class="text-on-surface">${formatNum(item.comments)}</strong></span>
                <span class="text-[10px] text-on-surface-variant flex items-center gap-1"><span class="material-symbols-outlined text-sm">share</span><strong class="text-on-surface">${formatNum(item.shares)}</strong></span>
            </div>
            <div class="flex gap-1 flex-wrap justify-end">${hashtags.map(h => '<span class="px-1.5 py-0.5 text-[9px] rounded bg-surface-container-highest text-on-surface-variant">#' + escapeHTML(h) + '</span>').join('')}</div>
        </div>
      </div>`;
    }

    /* ===== SEARCH ===== */
    async function runSearch(){
      const q = document.getElementById('keywordInput').value.trim();
      const platform = document.getElementById('platformSelect').value;
      const sort = document.getElementById('sortBy').value;
      const dateRange = document.getElementById('dateRange').value;
      const minViews = document.getElementById('minViews').value;
      const maxViews = document.getElementById('maxViews').value;
      const minLikes = document.getElementById('minLikes').value;
      const maxLikes = document.getElementById('maxLikes').value;
      const params = new URLSearchParams({ keyword: q, platforms: platform, max_results: '5', sort, date_range: dateRange });
      if(minViews) params.set('min_views', minViews);
      if(maxViews) params.set('max_views', maxViews);
      if(minLikes) params.set('min_likes', minLikes);
      if(maxLikes) params.set('max_likes', maxLikes);
      document.getElementById('searchMeta').innerHTML = '> Menjalankan kueri ke server...';
      const jsonLink = document.getElementById('jsonDownload');
      const csvLink = document.getElementById('csvDownload');
      jsonLink.classList.add('hidden');
      csvLink.classList.add('hidden');

      try {
          const res = await fetch('/api/search?' + params.toString());
          const data = await res.json();

          if (!res.ok) {
              if (res.status === 429 || res.status === 402 || res.status === 400) {
                   document.getElementById('searchMeta').innerHTML = `<span class="text-error font-bold">> TERTOLAK: ${escapeHTML(data.error)}</span> <a href="${data.upgrade_url || '/payment'}" class="ml-2 inline-block bg-primary text-on-primary-fixed px-3 py-1 rounded text-[10px] font-bold uppercase tracking-wider">Upgrade Sekarang</a>`;
                   document.getElementById('searchResults').innerHTML = '';
                   return;
              }
              throw new Error(data.error || 'Server error');
          }

          document.getElementById('searchMeta').textContent = `> SUCCESS: ${data.results?.length || 0} hasil ditangkap.`;
          document.getElementById('searchResults').innerHTML = (data.results || []).map(rowResult).join('') || '<div class="p-4 text-sm text-on-surface-variant">Belum ada hasil.</div>';
          if(data.json_file){ jsonLink.href = '/api/download?file=' + encodeURIComponent(data.json_file); jsonLink.classList.remove('hidden'); }
          if(data.csv_file){ csvLink.href = '/api/download?file=' + encodeURIComponent(data.csv_file); csvLink.classList.remove('hidden'); }
      } catch (err) {
          document.getElementById('searchMeta').innerHTML = `<span class="text-error">> ERROR: ${escapeHTML(err.message)}</span>`;
      }
    }

    /* ===== PROFILE ===== */
    async function loadProfile(){
      const username = document.getElementById('profileInput').value.trim();
      const sort = document.getElementById('profileSort').value;
      const dateRange = document.getElementById('profileDateRange').value;
      try {
          const res = await fetch(`/api/profile?username=${encodeURIComponent(username)}&max_results=5&sort=${encodeURIComponent(sort)}&date_range=${encodeURIComponent(dateRange)}`);
          if(!res.ok) throw new Error("Gagal mengambil profil.");
          const data = await res.json();
          const results = data.results || [];
          document.getElementById('profileResults').innerHTML = results.map(rowResult).join('') || '<div class="p-4 text-sm text-on-surface-variant">Belum ada hasil profil.</div>';
          document.getElementById('profileAnalytics').innerHTML = `<h4 class="font-headline text-lg font-bold mb-3 text-primary">Intelligence Summary</h4><p class="text-sm text-on-surface-variant">${results.length} konten dianalisis dari @<span class="font-bold text-on-surface">${escapeHTML(username)}</span>. Pattern siap disalin.</p>`;
      } catch(err) {
          document.getElementById('profileAnalytics').innerHTML = `<p class="text-error">${err.message}</p>`;
      }
    }

    /* ===== COMMENTS ===== */
    async function loadComments(){
      const videoUrl = document.getElementById('commentsUrl').value.trim();
      const max = document.getElementById('commentsMax').value || '3';
      document.getElementById('commentsMeta').textContent = '> Menghubungkan node ekstraksi komentar...';
      try {
          const res = await fetch(`/api/comments?video_url=${encodeURIComponent(videoUrl)}&max_comments=${encodeURIComponent(max)}`);
          const data = await res.json();
          document.getElementById('commentsMeta').textContent = `> SUCCESS: Diekstrak ${data.total || 0} komentar. Total real count: ${data.video_comment_count ?? '-'}`;
          document.getElementById('commentsResults').innerHTML = (data.comments || []).map(c => `
          <div class="bg-surface-container-highest rounded-lg p-4 border-l-2 border-primary">
              <div class="font-bold text-xs text-primary mb-1">${escapeHTML(c.nickname || c.user || 'User')}</div>
              <p class="mt-1 text-sm text-on-surface leading-snug">${escapeHTML(c.text || '')}</p>
          </div>`).join('') || '<div class="text-sm text-on-surface-variant">Belum ada komentar.</div>';
      } catch(err) {
          document.getElementById('commentsMeta').textContent = `> ERROR: Ekstraksi gagal.`;
      }
    }

    document.getElementById('searchBtn').addEventListener('click', runSearch);
    document.getElementById('profileLoadBtn').addEventListener('click', loadProfile);
    document.getElementById('commentsLoadBtn').addEventListener('click', loadComments);
</script>
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
