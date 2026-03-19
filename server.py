#!/usr/bin/env python3
import asyncio
import base64
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from functools import partial

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

app = FastAPI(title="Social Media Scraper")
executor = ThreadPoolExecutor(max_workers=5)
SCRAPE_TIMEOUT_SECONDS = int(os.getenv("SCRAPE_TIMEOUT_SECONDS", "45"))
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
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com data:; "
        "script-src 'self' 'unsafe-inline'; "
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
    "ringan": {
        "name": "Paket Ringan",
        "price_idr": 59_000,
        "tagline": "Buat mulai rutin tanpa berat di biaya.",
        "limits": [
            "30 pencarian per bulan",
            "10 cek profil",
            "10 tarik komentar",
            "10 transkrip video",
        ],
        "cta": "Mulai Paket Ringan",
        "env_key": "MAYAR_URL_RINGAN",
        "accent": "sun",
    },
    "tumbuh": {
        "name": "Paket Tumbuh",
        "price_idr": 99_000,
        "tagline": "Pilihan paling pas buat pemakaian rutin.",
        "limits": [
            "120 pencarian per bulan",
            "40 cek profil",
            "40 tarik komentar",
            "40 transkrip video",
        ],
        "cta": "Ambil Paket Tumbuh",
        "env_key": "MAYAR_URL_TUMBUH",
        "accent": "ember",
    },
    "tim": {
        "name": "Paket Tim",
        "price_idr": 299_000,
        "tagline": "Untuk workflow tim kecil yang udah serius.",
        "limits": [
            "500 pencarian per bulan",
            "150 cek profil",
            "150 tarik komentar",
            "150 transkrip video",
            "3 anggota tim",
        ],
        "cta": "Ambil Paket Tim",
        "env_key": "MAYAR_URL_TIM",
        "accent": "forest",
    },
}


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


async def enforce_feature_access(request: Request, feature: str) -> tuple[dict | None, dict | None, JSONResponse | None]:
    if not supabase_auth_configured():
        return None, None, None

    user = await get_authenticated_user(request)
    if not user:
        return None, None, JSONResponse(
            {"error": "Silakan login dulu untuk memakai fitur ini.", "code": "auth_required"},
            status_code=401,
        )

    if not supabase_rest_configured():
        return user, None, None

    subscription, plan = await fetch_current_subscription(user["id"])
    if not subscription_is_active(subscription) or not plan:
        return user, None, JSONResponse(
            {
                "error": "Paket aktif belum ditemukan. Pilih paket dulu untuk lanjut pakai aplikasi.",
                "code": "subscription_required",
                "upgrade_url": "/payment",
            },
            status_code=402,
        )

    limit_map = {
        "search": "monthly_search_limit",
        "profile": "monthly_profile_limit",
        "comments": "monthly_comment_limit",
        "transcript": "monthly_transcript_limit",
    }
    limit_field = limit_map.get(feature)
    if not limit_field:
        return user, plan, None

    limit_value = int(plan.get(limit_field) or 0)
    if limit_value <= 0:
        return user, plan, None

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
async def auth_signup(payload: dict = Body(...)):
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
async def auth_signin(payload: dict = Body(...)):
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
    if supabase_auth_configured():
        user = await get_authenticated_user(request)
        if not user:
            return RedirectResponse("/signin", status_code=302)
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
    cached = SEARCH_CACHE.get(cache_key)
    if cached and time.time() - cached[0] < SEARCH_CACHE_TTL_SECONDS:
        return {
            **cached[1],
            "cached": True,
            "elapsed": "<1s (cached)",
        }

    platform_list = [p.strip() for p in platforms.split(",") if p.strip() in SCRAPERS]
    if not platform_list:
        return JSONResponse({"error": "No valid platforms"}, 400)

    # Support multiple keywords separated by newlines
    keywords = [k.strip() for k in query_value.split("\n") if k.strip()]
    if not keywords:
        return JSONResponse({"error": "No keywords provided"}, 400)

    loop = asyncio.get_event_loop()
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
        if name == "tiktok":
            work = partial(
                scraper.search,
                keyword,
                max_value,
                sort=sort,
                min_likes=min_likes,
                max_likes=max_likes,
            )
        else:
            work = partial(scraper.search, keyword, max_value)

        try:
            results = await asyncio.wait_for(
                loop.run_in_executor(executor, work),
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
    if all_results:
        json_file, csv_file = save_results(all_results, keywords[0])

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

    loop = asyncio.get_event_loop()
    start = time.time()

    scraper = TikTokScraper()
    results = await loop.run_in_executor(
        executor, partial(scraper.scrape_profile, username, max_value, sort)
    )
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

    loop = asyncio.get_event_loop()
    scraper = TikTokScraper()
    result = await loop.run_in_executor(
        executor, partial(scraper.scrape_comments, target_url, max_value)
    )
    video_comment_count = await loop.run_in_executor(
        executor, partial(scraper.get_video_comment_count, target_url)
    )
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

    candidate = Path(requested)
    if candidate.is_absolute():
        return JSONResponse({"error": "Invalid file path"}, 400)

    safe_path = (Path.cwd() / candidate).resolve()
    try:
        safe_path.relative_to(OUTPUT_DIR)
    except ValueError:
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
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Sinyal - Content Intelligence Workspace</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=Space+Grotesk:wght@500;700&display=swap" rel="stylesheet">
<style>
:root {
  --bg: #f3ecdf;
  --bg-card: rgba(255,255,255,0.78);
  --bg-card-hover: rgba(255,255,255,0.92);
  --bg-input: rgba(255,255,255,0.72);
  --border: rgba(79,49,27,0.12);
  --border-hover: rgba(79,49,27,0.24);
  --text: #1f1a17;
  --text-secondary: #5d5349;
  --text-muted: #8a7b6d;
  --primary: #d9481f;
  --primary-hover: #b53b19;
  --primary-bg: rgba(217,72,31,0.12);
  --accent-tiktok: #ff0050;
  --accent-youtube: #ff0000;
  --accent-instagram: #d946ef;
  --accent-twitter: #1d9bf0;
  --accent-facebook: #1877f2;
  --success: #22c55e;
  --radius: 18px;
  --radius-sm: 8px;
  --radius-lg: 28px;
}

* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: 'IBM Plex Sans', sans-serif;
  background:
    radial-gradient(circle at top left, rgba(217,72,31,0.18), transparent 32%),
    radial-gradient(circle at top right, rgba(255,178,102,0.28), transparent 28%),
    linear-gradient(180deg, #fbf6ee 0%, #f3ecdf 52%, #efe4d2 100%);
  color: var(--text);
  min-height: 100vh;
  overflow-x: hidden;
}

/* Subtle grid background */
body::before {
  content: '';
  position: fixed;
  inset: 0;
  background-image:
    linear-gradient(rgba(79,49,27,0.03) 1px, transparent 1px),
    linear-gradient(90deg, rgba(79,49,27,0.03) 1px, transparent 1px);
  background-size: 42px 42px;
  pointer-events: none;
  z-index: 0;
  mask-image: linear-gradient(180deg, rgba(0,0,0,0.65), transparent 92%);
}

.app { position: relative; z-index: 1; }

/* NAV */
nav {
  border-bottom: 1px solid rgba(79,49,27,0.08);
  padding: 16px 32px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  backdrop-filter: blur(18px);
  background: rgba(251,246,238,0.7);
  position: sticky;
  top: 0;
  z-index: 100;
}
.logo {
  font-family: 'Space Grotesk', sans-serif;
  font-size: 24px;
  font-weight: 700;
  letter-spacing: -0.04em;
}
.logo span { color: var(--primary); }
.nav-links { display: flex; gap: 4px; }
.nav-link {
  padding: 8px 16px;
  border-radius: var(--radius-sm);
  font-size: 14px;
  font-weight: 500;
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.2s;
  border: none;
  background: none;
}
.nav-link:hover, .nav-link.active { color: var(--text); background: rgba(255,255,255,0.72); }

/* HERO */
.hero {
  text-align: center;
  padding: 76px 32px 40px;
  max-width: 920px;
  margin: 0 auto;
}
.hero-kicker {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  border-radius: 999px;
  border: 1px solid rgba(79,49,27,0.1);
  background: rgba(255,255,255,0.66);
  color: var(--text-secondary);
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 20px;
}
.hero h1 {
  font-family: 'Space Grotesk', sans-serif;
  font-size: 58px;
  font-weight: 700;
  letter-spacing: -0.06em;
  line-height: 1;
  margin-bottom: 18px;
}
.hero h1 .gradient {
  background: linear-gradient(135deg, #d9481f, #f97316, #f59e0b);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}
.hero p {
  color: var(--text-secondary);
  font-size: 18px;
  line-height: 1.7;
  max-width: 720px;
  margin: 0 auto;
}
.hero-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
  margin-top: 28px;
}
.hero-metric {
  padding: 18px;
  border-radius: 18px;
  background: rgba(255,255,255,0.72);
  border: 1px solid rgba(79,49,27,0.08);
  text-align: left;
}
.hero-metric strong {
  display: block;
  font-family: 'Space Grotesk', sans-serif;
  font-size: 24px;
  margin-bottom: 4px;
}
.hero-metric span { color: var(--text-secondary); font-size: 13px; line-height: 1.5; }

/* MAIN CONTAINER */
.main { max-width: 1120px; margin: 0 auto; padding: 0 32px 72px; }
.account-strip {
  max-width: 1120px;
  margin: -10px auto 22px;
  padding: 0 32px;
}
.account-panel {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 14px;
  align-items: center;
  background: rgba(255,255,255,0.82);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 18px 20px;
  box-shadow: 0 18px 40px rgba(107, 74, 47, 0.08);
}
.account-copy strong {
  display: block;
  font-size: 16px;
  margin-bottom: 4px;
}
.account-copy p {
  color: var(--text-secondary);
  font-size: 13px;
  line-height: 1.6;
}
.account-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  justify-content: flex-end;
}
.usage-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}
.usage-chip {
  border-radius: 999px;
  padding: 8px 12px;
  background: rgba(217,72,31,0.08);
  border: 1px solid rgba(217,72,31,0.12);
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 600;
}
.usage-chip strong {
  color: var(--primary-hover);
  margin-right: 6px;
}
.banner-inline {
  margin-bottom: 16px;
  padding: 14px 16px;
  border-radius: 18px;
  border: 1px solid rgba(217,72,31,0.12);
  background: rgba(217,72,31,0.08);
  color: var(--text-secondary);
  font-size: 13px;
  line-height: 1.6;
}
.banner-inline strong {
  color: var(--text);
}
.hidden { display: none !important; }

/* TABS / PAGES */
.page { display: none; }
.page.active { display: block; }

/* SECTION CARD */
.section {
  background: var(--bg-card);
  border: 1px solid var(--border);
  backdrop-filter: blur(14px);
  box-shadow: 0 18px 40px rgba(107, 74, 47, 0.08);
  border-radius: var(--radius-lg);
  padding: 28px;
  margin-bottom: 20px;
}
.section-title {
  font-size: 15px;
  font-weight: 600;
  margin-bottom: 20px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.section-title .icon {
  width: 32px;
  height: 32px;
  border-radius: var(--radius-sm);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
}

/* FORM ELEMENTS */
.form-group { margin-bottom: 16px; }
.form-label {
  display: block;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
  margin-bottom: 6px;
}
.form-hint {
  font-size: 12px;
  color: var(--text-muted);
  margin-top: 4px;
}

input[type="text"], input[type="number"], textarea, select {
  width: 100%;
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-input);
  color: var(--text);
  font-size: 14px;
  font-family: inherit;
  outline: none;
  transition: border-color 0.2s;
}
input:focus, textarea:focus, select:focus { border-color: var(--primary); }
textarea { resize: vertical; min-height: 100px; }
select { cursor: pointer; appearance: none; background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' fill='%2371717a' viewBox='0 0 16 16'%3E%3Cpath d='M8 11L3 6h10z'/%3E%3C/svg%3E"); background-repeat: no-repeat; background-position: right 12px center; padding-right: 36px; }

/* CHIPS / TOGGLES */
.chip-group { display: flex; gap: 8px; flex-wrap: wrap; }
.chip {
  padding: 8px 16px;
  border: 1px solid var(--border);
  border-radius: 100px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s;
  user-select: none;
  display: flex;
  align-items: center;
  gap: 6px;
}
.chip:hover { border-color: var(--border-hover); }
.chip.active { border-color: var(--primary); background: var(--primary-bg); color: var(--primary-hover); }
.chip .dot { width: 8px; height: 8px; border-radius: 50%; }
.quick-picks {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 14px;
}
.quick-pick {
  border: 1px dashed rgba(79,49,27,0.18);
  background: rgba(255,255,255,0.55);
  color: var(--text-secondary);
  border-radius: 999px;
  padding: 10px 14px;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s;
}
.quick-pick:hover {
  border-color: var(--primary);
  color: var(--primary-hover);
  transform: translateY(-1px);
}

/* INLINE GRID */
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.grid-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; }
@media (max-width: 640px) { .grid-2, .grid-3 { grid-template-columns: 1fr; } }

/* BUTTON */
.btn {
  padding: 12px 24px;
  border: none;
  border-radius: var(--radius-sm);
  font-size: 14px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.2s;
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.btn-primary {
  background: var(--primary);
  color: white;
  width: 100%;
  justify-content: center;
  padding: 16px;
  font-size: 15px;
  border-radius: var(--radius);
  box-shadow: 0 16px 30px rgba(217,72,31,0.22);
}
.btn-primary:hover { background: var(--primary-hover); }
.btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-secondary {
  background: var(--bg-card);
  color: var(--text);
  border: 1px solid var(--border);
}
.btn-secondary:hover { background: var(--bg-card-hover); border-color: var(--border-hover); }

/* STATUS BAR */
.status-bar {
  padding: 14px 20px;
  border-radius: var(--radius);
  background: rgba(255,255,255,0.75);
  border: 1px solid var(--border);
  margin-bottom: 20px;
  display: none;
  align-items: center;
  gap: 12px;
  font-size: 14px;
}
.status-bar.active { display: flex; }
.spinner {
  width: 18px; height: 18px;
  border: 2px solid var(--border);
  border-top-color: var(--primary);
  border-radius: 50%;
  animation: spin 0.7s linear infinite;
  flex-shrink: 0;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* RESULTS */
.stats-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 12px;
  margin-bottom: 20px;
}
.stat-card {
  background: rgba(255,255,255,0.8);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 20px;
  text-align: center;
}
.stat-card .value { font-size: 28px; font-weight: 700; }
.stat-card .label { font-size: 12px; color: var(--text-muted); margin-top: 4px; text-transform: uppercase; letter-spacing: 0.5px; }
.stat-card.tiktok .value { color: var(--accent-tiktok); }
.stat-card.youtube .value { color: var(--accent-youtube); }
.stat-card.instagram .value { color: var(--accent-instagram); }
.stat-card.twitter .value { color: var(--accent-twitter); }
.stat-card.facebook .value { color: var(--accent-facebook); }

.download-row {
  display: flex;
  gap: 10px;
  margin-bottom: 20px;
}
.download-btn {
  padding: 10px 20px;
  border-radius: var(--radius-sm);
  font-size: 13px;
  font-weight: 500;
  text-decoration: none;
  border: 1px solid var(--border);
  color: var(--text-secondary);
  background: var(--bg-card);
  transition: all 0.2s;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.download-btn:hover { border-color: var(--primary); color: var(--primary-hover); }

/* RESULT CARDS */
.result-list { display: flex; flex-direction: column; gap: 8px; }
.result-card {
  background: rgba(255,255,255,0.82);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 16px 20px;
  transition: all 0.15s;
  cursor: default;
}
.result-card:hover { border-color: var(--border-hover); background: var(--bg-card-hover); }
.result-top { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.badge {
  padding: 3px 10px;
  border-radius: 100px;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.badge.tiktok { background: rgba(255,0,80,0.1); color: var(--accent-tiktok); }
.badge.youtube { background: rgba(255,0,0,0.1); color: var(--accent-youtube); }
.badge.instagram { background: rgba(224,64,251,0.1); color: var(--accent-instagram); }
.badge.twitter { background: rgba(29,155,240,0.1); color: var(--accent-twitter); }
.badge.facebook { background: rgba(24,119,242,0.1); color: var(--accent-facebook); }
.result-title {
  font-size: 14px;
  font-weight: 500;
  margin-bottom: 4px;
  line-height: 1.4;
}
.result-title a { color: var(--text); text-decoration: none; }
.result-title a:hover { color: var(--primary-hover); }
.result-meta { font-size: 13px; color: var(--text-muted); margin-bottom: 10px; }
.result-copy-grid {
  display: grid;
  gap: 10px;
  margin: 10px 0 12px;
}
.result-copy-block {
  padding: 12px 14px;
  border-radius: 14px;
  background: rgba(217,72,31,0.06);
  border: 1px solid rgba(217,72,31,0.08);
}
.result-copy-block.alt {
  background: rgba(255,255,255,0.78);
}
.result-copy-block strong {
  color: var(--text);
  display: block;
  margin-bottom: 4px;
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.result-copy-block p {
  color: var(--text-secondary);
  font-size: 13px;
  line-height: 1.6;
}
.result-copy-block.empty p {
  color: var(--text-muted);
}
.result-transcript {
  padding: 12px 14px;
  border-radius: 14px;
  background: rgba(217,72,31,0.06);
  color: var(--text-secondary);
  font-size: 13px;
  line-height: 1.6;
  border: 1px solid rgba(217,72,31,0.08);
}
.result-transcript strong {
  color: var(--text);
  display: block;
  margin-bottom: 4px;
}
.result-copy-meta {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}
.copy-chip {
  padding: 5px 10px;
  border-radius: 999px;
  background: rgba(255,255,255,0.82);
  border: 1px solid rgba(79,49,27,0.08);
  color: var(--text-muted);
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
}
.result-stats {
  display: flex;
  gap: 20px;
  font-size: 12px;
  color: var(--text-secondary);
  flex-wrap: wrap;
}
.result-stats .stat { display: flex; align-items: center; gap: 4px; }
.badge.sponsored { background: rgba(31,41,55,0.08); color: #5b4633; }
.badge.analytics { background: rgba(217,72,31,0.12); color: var(--primary); }
.result-badges {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.profile-shell {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 310px;
  gap: 20px;
  align-items: start;
}
.profile-main { min-width: 0; }
.analytics-rail {
  position: sticky;
  top: 92px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.analytics-panel {
  background: rgba(255,255,255,0.88);
  border: 1px solid var(--border);
  border-radius: 24px;
  padding: 18px;
  box-shadow: 0 18px 40px rgba(107, 74, 47, 0.08);
}
.analytics-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 14px;
}
.analytics-header h3 {
  font-family: 'Space Grotesk', sans-serif;
  font-size: 20px;
  letter-spacing: -0.04em;
}
.analytics-header p {
  color: var(--text-muted);
  font-size: 12px;
}
.analytics-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
.analytics-metric {
  border: 1px solid rgba(79,49,27,0.08);
  border-radius: 18px;
  padding: 14px;
  background: rgba(255,255,255,0.72);
}
.analytics-metric strong {
  display: block;
  font-family: 'Space Grotesk', sans-serif;
  font-size: 22px;
  margin-bottom: 2px;
}
.analytics-metric span {
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1.4;
}
.analytics-split {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.analytics-split-row {
  display: flex;
  justify-content: space-between;
  gap: 14px;
  padding: 12px 0;
  border-top: 1px solid rgba(79,49,27,0.08);
}
.analytics-split-row:first-child { border-top: none; padding-top: 0; }
.analytics-split-row strong {
  display: block;
  font-size: 14px;
}
.analytics-split-row span {
  color: var(--text-muted);
  font-size: 12px;
}
.analytics-empty {
  color: var(--text-muted);
  font-size: 13px;
  line-height: 1.6;
}
.filter-bar {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 180px;
  gap: 12px;
  margin-bottom: 14px;
}
.filter-summary {
  color: var(--text-muted);
  font-size: 13px;
  margin-bottom: 12px;
}

/* COMMENT CARDS */
.comment-card {
  background: rgba(255,255,255,0.8);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 14px 18px;
  margin-bottom: 8px;
}
.comment-user { font-size: 13px; font-weight: 600; color: var(--primary-hover); margin-bottom: 4px; }
.comment-text { font-size: 14px; line-height: 1.5; }
.comment-meta { font-size: 12px; color: var(--text-muted); margin-top: 6px; }
@media (max-width: 800px) {
  .hero { padding-top: 54px; }
  .hero h1 { font-size: 42px; }
  .hero-metrics { grid-template-columns: 1fr; }
  nav { padding: 14px 20px; flex-direction: column; align-items: stretch; gap: 12px; }
  .nav-links { width: 100%; overflow-x: auto; padding-bottom: 2px; }
  .main { padding: 0 20px 48px; }
  .profile-shell { grid-template-columns: 1fr; }
  .analytics-rail { position: static; }
  .filter-bar { grid-template-columns: 1fr; }
  .download-row { flex-direction: column; }
  .download-btn { width: 100%; justify-content: center; }
}

@media (max-width: 640px) {
  .hero { padding: 36px 20px 18px; }
  .hero h1 { font-size: 34px; line-height: 1.08; }
  .hero p { font-size: 15px; }
  .section { padding: 20px; border-radius: 22px; }
  textarea { min-height: 128px; }
  .quick-picks { gap: 8px; }
  .quick-pick { width: 100%; justify-content: center; text-align: center; }
  .result-card { padding: 14px 16px; }
  .result-top { align-items: flex-start; gap: 8px; flex-direction: column; }
}
</style>
</head>
<body>
<div class="app">

<nav>
  <div class="logo">Sin<span>yal</span></div>
  <div class="nav-links">
    <button class="nav-link active" onclick="switchPage('search', this)">Riset</button>
    <button class="nav-link" onclick="switchPage('profile', this)">Profil</button>
    <button class="nav-link" onclick="switchPage('comments', this)">Komentar</button>
  </div>
</nav>

<div class="hero">
  <div class="hero-kicker">Content Intelligence untuk Creator Indonesia</div>
  <h1>Bongkar <span class="gradient">pola konten yang jalan</span>.</h1>
  <p>Riset hook, caption, transcript, komentar, dan pola performa konten publik dari TikTok, YouTube, Instagram, X, dan Facebook dalam satu workspace yang enak dipakai.</p>
  <div class="hero-metrics">
    <div class="hero-metric"><strong>Hook & caption</strong><span>Lihat opening line, caption, dan angle yang paling sering dipakai tanpa buka banyak tab.</span></div>
    <div class="hero-metric"><strong>Signals, bukan gimmick</strong><span>Fokus ke pola konten publik yang benar-benar kelihatan, bukan angka estimasi yang ngawang.</span></div>
    <div class="hero-metric"><strong>Siap dipakai tim</strong><span>Ekspor hasil riset dengan cepat buat creator, affiliate marketer, atau tim konten.</span></div>
  </div>
</div>

<div class="main">

<!-- ==================== SEARCH PAGE ==================== -->
<div class="page active" id="page-search">
  <div class="banner-inline hidden" id="searchBanner"></div>

  <div class="section">
    <div class="section-title"><div class="icon" style="background:var(--primary-bg)">S</div> Search Workspace</div>
    <div class="form-group">
      <label class="form-label">Cari topik, brand, masalah, atau creator</label>
      <textarea id="keywords" placeholder="Contoh:&#10;skincare viral&#10;kopi susu literan&#10;lowongan kerja remote&#10;openai"></textarea>
      <div class="form-hint">Bisa isi banyak keyword, satu baris satu query. Cocok buat ngetes beberapa angle sekaligus.</div>
      <div class="quick-picks">
        <button class="quick-pick" type="button" onclick="applyExample('skincare viral')">skincare viral</button>
        <button class="quick-pick" type="button" onclick="applyExample('kopi susu literan')">kopi susu literan</button>
        <button class="quick-pick" type="button" onclick="applyExample('UMKM fashion lokal')">UMKM fashion lokal</button>
        <button class="quick-pick" type="button" onclick="applyExample('AI untuk kerja')">AI untuk kerja</button>
      </div>
    </div>
  </div>

  <div class="section">
    <div class="section-title"><div class="icon" style="background:rgba(245,158,11,0.14)">P</div> Platforms</div>
    <div class="chip-group">
      <div class="chip active" data-platform="tiktok" onclick="toggleChip(this)"><span class="dot" style="background:var(--accent-tiktok)"></span>TikTok</div>
      <div class="chip active" data-platform="youtube" onclick="toggleChip(this)"><span class="dot" style="background:var(--accent-youtube)"></span>YouTube</div>
      <div class="chip active" data-platform="instagram" onclick="toggleChip(this)"><span class="dot" style="background:var(--accent-instagram)"></span>Instagram</div>
      <div class="chip active" data-platform="twitter" onclick="toggleChip(this)"><span class="dot" style="background:var(--accent-twitter)"></span>Twitter/X</div>
      <div class="chip active" data-platform="facebook" onclick="toggleChip(this)"><span class="dot" style="background:var(--accent-facebook)"></span>Facebook</div>
    </div>
  </div>

  <div class="section">
    <div class="section-title"><div class="icon" style="background:rgba(34,197,94,0.14)">F</div> Ranking & Filters</div>
    <div class="grid-3">
      <div class="form-group">
        <label class="form-label">Results per platform</label>
        <input type="number" id="maxResults" value="3" min="1" max="50">
      </div>
      <div class="form-group">
        <label class="form-label">Urutkan hasil</label>
        <select id="sortBy">
          <option value="relevance">Paling relevan</option>
          <option value="popular">Views tertinggi</option>
          <option value="most_liked">Likes tertinggi</option>
          <option value="latest">Terbaru</option>
        </select>
      </div>
      <div class="form-group">
        <label class="form-label">Date range</label>
        <select id="searchDateRange">
          <option value="all">All time</option>
          <option value="7d">Last 7 days</option>
          <option value="30d">Last 30 days</option>
        </select>
      </div>
    </div>
    <div class="grid-2">
      <div class="form-group">
        <label class="form-label">Min views</label>
        <input type="number" id="minViews" placeholder="No minimum">
      </div>
      <div class="form-group">
        <label class="form-label">Max views</label>
        <input type="number" id="maxViews" placeholder="No maximum">
      </div>
    </div>
    <div class="grid-2">
      <div class="form-group">
        <label class="form-label">Min likes</label>
        <input type="number" id="minLikes" placeholder="No minimum">
      </div>
      <div class="form-group">
        <label class="form-label">Max likes</label>
        <input type="number" id="maxLikes" placeholder="No maximum">
      </div>
    </div>
  </div>

  <button class="btn btn-primary" id="searchBtn" onclick="doSearch()">Cari Sinyal Sosial</button>

  <div style="height:24px"></div>
  <div class="status-bar" id="searchStatus"><div class="spinner"></div><span id="searchStatusText"></span></div>
  <div id="searchStats"></div>
  <div id="searchDownloads"></div>
  <div id="searchResults" class="result-list"></div>
</div>

<!-- ==================== PROFILE PAGE ==================== -->
<div class="page" id="page-profile">
  <div class="banner-inline hidden" id="profileBanner"></div>

  <div class="section">
    <div class="section-title"><div class="icon" style="background:rgba(255,0,80,0.1)">@</div> TikTok Profile Deep Dive</div>
    <div class="grid-2">
      <div class="form-group">
        <label class="form-label">Username TikTok</label>
        <input type="text" id="profileUsername" placeholder="username saja, tanpa @">
      </div>
      <div class="form-group">
        <label class="form-label">Max videos</label>
        <input type="number" id="profileMax" value="10" min="1" max="50">
      </div>
    </div>
    <div class="form-group">
      <label class="form-label">Urutkan video</label>
      <select id="profileSort">
        <option value="latest">Terbaru</option>
        <option value="popular">Paling populer</option>
        <option value="oldest">Terlama</option>
      </select>
    </div>
    <div class="form-group">
      <label class="form-label">Date range</label>
      <select id="profileDateRange">
        <option value="all">All time</option>
        <option value="7d">Last 7 days</option>
        <option value="30d">Last 30 days</option>
      </select>
    </div>
  </div>

  <button class="btn btn-primary" id="profileBtn" onclick="doProfile()">Buka Profil</button>

  <div style="height:24px"></div>
  <div class="status-bar" id="profileStatus"><div class="spinner"></div><span id="profileStatusText"></span></div>
  <div class="profile-shell">
    <div class="profile-main">
      <div id="profileStats"></div>
      <div id="profileDownloads"></div>
      <div class="section">
        <div class="section-title"><div class="icon" style="background:rgba(217,72,31,0.12)">F</div> Feed Breakdown</div>
        <div class="filter-summary" id="profileFilterSummary">Buka profil untuk lihat pola konten, post sponsor vs organik, dan cari angle yang sering dipakai.</div>
        <div class="filter-bar">
          <input type="text" id="profileFeedSearch" placeholder="Search post titles or captions..." oninput="applyProfileFilters()">
          <select id="profileSponsoredFilter" onchange="applyProfileFilters()">
            <option value="all">All posts</option>
            <option value="organic">Organic only</option>
            <option value="sponsored">Sponsored only</option>
          </select>
        </div>
        <div id="profileResults" class="result-list"></div>
      </div>
    </div>
    <aside class="analytics-rail">
      <div id="profileAnalytics" class="analytics-panel">
        <div class="analytics-header">
          <h3>Profile signals</h3>
          <p>side rail</p>
        </div>
        <div class="analytics-empty">Belum ada data. Buka satu profil TikTok dulu untuk lihat sinyal konten, rata-rata performa, dan pola organik vs sponsor.</div>
      </div>
    </aside>
  </div>
</div>

<!-- ==================== COMMENTS PAGE ==================== -->
<div class="page" id="page-comments">
  <div class="banner-inline hidden" id="commentBanner"></div>

  <div class="section">
    <div class="section-title"><div class="icon" style="background:rgba(34,197,94,0.1)">C</div> TikTok Comment Readout</div>
    <div class="grid-2">
      <div class="form-group">
        <label class="form-label">Video URL</label>
        <input type="text" id="commentUrl" placeholder="https://www.tiktok.com/@user/video/123...">
      </div>
      <div class="form-group">
        <label class="form-label">Max comments</label>
        <input type="number" id="commentMax" value="50" min="1" max="200">
      </div>
    </div>
  </div>

  <button class="btn btn-primary" id="commentBtn" onclick="doComments()">Ambil Komentar</button>

  <div style="height:24px"></div>
  <div class="status-bar" id="commentStatus"><div class="spinner"></div><span id="commentStatusText"></span></div>
  <div id="commentResults"></div>
</div>

</div><!-- .main -->
</div><!-- .app -->

<script>
function switchPage(name, el) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-link').forEach(n => n.classList.remove('active'));
  document.getElementById('page-' + name).classList.add('active');
  el.classList.add('active');
}

function toggleChip(el) { el.classList.toggle('active'); }
function applyExample(value) { document.getElementById('keywords').value = value; }
let profileResultsState = [];
let sessionState = null;
let usageState = null;
const SPONSORED_TERMS = ['#ad', '#sponsored', 'sponsored', 'paid partnership', 'affiliate', 'kerjasama', 'promo', 'diskon', 'voucher'];

function escapeHtml(value) {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function fmt(n) {
  if (n == null) return '\u2014';
  if (n >= 1e6) return (n/1e6).toFixed(1) + 'M';
  if (n >= 1e3) return (n/1e3).toFixed(1) + 'K';
  return n.toLocaleString();
}
function pct(n) {
  if (n == null || Number.isNaN(n)) return '\u2014';
  return n.toFixed(2) + '%';
}
function avg(results, key) {
  const values = results.map(r => Number(r[key]) || 0).filter(v => v > 0);
  if (!values.length) return null;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}
function engagementRate(result) {
  const views = Number(result.views) || 0;
  if (!views) return null;
  const interactions = (Number(result.likes) || 0) + (Number(result.comments) || 0) + (Number(result.shares) || 0);
  return (interactions / views) * 100;
}
function isSponsoredPost(result) {
  const haystack = `${result.title || ''} ${result.description || ''} ${result.caption || ''} ${result.transcript || ''}`.toLowerCase();
  return SPONSORED_TERMS.some(term => haystack.includes(term));
}
function profileBadge(result) {
  const er = engagementRate(result);
  const sponsored = isSponsoredPost(result);
  return {
    er,
    sponsored,
  };
}

function showStatus(id, text) {
  const bar = document.getElementById(id);
  bar.classList.add('active');
  // Re-add spinner if missing
  if (!bar.querySelector('.spinner')) {
    const sp = document.createElement('div');
    sp.className = 'spinner';
    bar.prepend(sp);
  }
  bar.querySelector('span').textContent = text;
}
function hideStatus(id, text) {
  const bar = document.getElementById(id);
  if (text) bar.querySelector('span').textContent = text;
  const sp = bar.querySelector('.spinner');
  if (sp) sp.remove();
}
function setBanner(id, html = '') {
  const el = document.getElementById(id);
  if (!el) return;
  if (!html) {
    el.innerHTML = '';
    el.classList.add('hidden');
    return;
  }
  el.innerHTML = html;
  el.classList.remove('hidden');
}

async function refreshAccountState() {
  try {
    const sessionResp = await fetch('/api/auth/session');
    sessionState = await sessionResp.json();
    usageState = null;
    if (sessionState?.authenticated) {
      const usageResp = await fetch('/api/account/usage');
      if (usageResp.ok) usageState = await usageResp.json();
    }
  } catch (e) {
    sessionState = { configured: false };
  }
}

async function doSignout() {
  await fetch('/api/auth/signout', { method: 'POST' });
  window.location.href = '/signin';
}

function handleApiFailure(data, fallbackMessage, bannerId) {
  const message = data?.error || fallbackMessage;
  if (bannerId) {
    const cta = data?.upgrade_url ? ` <a href="${data.upgrade_url}">Buka paket</a>` : '';
    setBanner(bannerId, `<strong>${escapeHtml(message)}</strong>${cta}`);
  }
  return message;
}

function renderCopyBlocks(r) {
  const title = escapeHtml(r.title || r.video_url || '');
  const hook = escapeHtml(r.hook || r.title || r.caption || r.description || 'Belum ada hook yang bisa diringkas.');
  const content = escapeHtml(r.content || r.description || r.caption || 'Belum ada isi yang bisa dibaca dari hasil ini.');
  const caption = escapeHtml(r.caption || r.description || 'Belum ada caption yang kebaca.');
  const transcript = escapeHtml(r.transcript || '');
  const transcriptLabel = r.transcript_source === 'spoken_text' ? 'Transcript video' : 'Transcript video';
  const transcriptBlock = transcript
    ? `<div class="result-copy-block"><div class="result-copy-meta"><span class="copy-chip">${transcriptLabel}</span></div><strong>Transcript</strong><p>${transcript}</p></div>`
    : `<div class="result-copy-block empty"><strong>Transcript</strong><p>Belum ada transcript suara yang berhasil dibaca. Untuk platform ini, data yang ada baru caption atau deskripsi video.</p></div>`;

  return `
    <div class="result-copy-grid">
      <div class="result-copy-block alt"><strong>Hook</strong><p>${hook}</p></div>
      <div class="result-copy-block alt"><strong>Isi</strong><p>${content}</p></div>
      <div class="result-copy-block alt"><strong>Caption</strong><p>${caption}</p></div>
      ${transcriptBlock}
    </div>
  `;
}

function renderCards(containerId, results) {
  document.getElementById(containerId).innerHTML = results.map(r => `
    <div class="result-card">
      <div class="result-top">
        <span class="badge ${r.platform}">${r.platform}</span>
        <span style="font-size:12px;color:var(--text-muted)">${r.duration ? r.duration + 's' : ''}</span>
      </div>
      <div class="result-title"><a href="${escapeHtml(r.video_url)}" target="_blank">${escapeHtml(r.title || r.description?.slice(0,120) || r.video_url)}</a></div>
      <div class="result-meta">@${escapeHtml(r.author || 'unknown')}${r.music ? ' &middot; ' + escapeHtml(r.music) : ''}</div>
      ${renderCopyBlocks(r)}
      <div class="result-stats">
        <span class="stat"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg> ${fmt(r.views)}</span>
        <span class="stat"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg> ${fmt(r.likes)}</span>
        <span class="stat"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg> ${fmt(r.comments)}</span>
        <span class="stat"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/><polyline points="16 6 12 2 8 6"/><line x1="12" y1="2" x2="12" y2="15"/></svg> ${fmt(r.shares)}</span>
        <span class="stat"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg> ${fmt(r.saves)}</span>
      </div>
    </div>
  `).join('');
}

function renderProfileCards(containerId, results) {
  document.getElementById(containerId).innerHTML = results.map(r => {
    const badge = profileBadge(r);
    return `
    <div class="result-card">
      <div class="result-top">
        <div class="result-badges">
          <span class="badge ${r.platform}">${r.platform}</span>
          <span class="badge analytics">${pct(badge.er)} ER</span>
          ${badge.sponsored ? '<span class="badge sponsored">Sponsored</span>' : '<span class="badge sponsored">Organic</span>'}
        </div>
        <span style="font-size:12px;color:var(--text-muted)">${r.duration ? r.duration + 's' : ''}</span>
      </div>
      <div class="result-title"><a href="${escapeHtml(r.video_url)}" target="_blank">${escapeHtml(r.title || r.description?.slice(0,120) || r.video_url)}</a></div>
      <div class="result-meta">@${escapeHtml(r.author || 'unknown')}${r.music ? ' &middot; ' + escapeHtml(r.music) : ''}</div>
      ${renderCopyBlocks(r)}
      <div class="result-stats">
        <span class="stat"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg> ${fmt(r.views)}</span>
        <span class="stat"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg> ${fmt(r.likes)}</span>
        <span class="stat"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg> ${fmt(r.comments)}</span>
        <span class="stat"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/><polyline points="16 6 12 2 8 6"/><line x1="12" y1="2" x2="12" y2="15"/></svg> ${fmt(r.shares)}</span>
      </div>
    </div>
  `;
  }).join('') || '<p style="color:var(--text-muted);padding:6px 2px">No posts match this filter.</p>';
}

function renderStats(containerId, results) {
  const platforms = {};
  results.forEach(r => {
    if (!platforms[r.platform]) platforms[r.platform] = { count: 0, views: 0 };
    platforms[r.platform].count++;
    platforms[r.platform].views += r.views || 0;
  });
  let html = '<div class="stats-row">';
  html += `<div class="stat-card"><div class="value" style="color:var(--primary)">${results.length}</div><div class="label">Total Results</div></div>`;
  for (const [p, s] of Object.entries(platforms)) {
    html += `<div class="stat-card ${p}"><div class="value">${s.count}</div><div class="label">${p} &middot; ${fmt(s.views)} views</div></div>`;
  }
  html += '</div>';
  document.getElementById(containerId).innerHTML = html;
}

function renderDownloads(containerId, json_file, csv_file) {
  if (!json_file) return;
  document.getElementById(containerId).innerHTML = `
    <div class="download-row">
      <a class="download-btn" href="/api/download?file=${encodeURIComponent(json_file)}">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
        Export JSON
      </a>
      <a class="download-btn" href="/api/download?file=${encodeURIComponent(csv_file)}">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
        Export CSV
      </a>
    </div>`;
}

function renderProfileAnalytics(results) {
  const sponsored = results.filter(isSponsoredPost);
  const organic = results.filter(r => !isSponsoredPost(r));
  const avgEngagement = avg(results.map(r => ({ value: engagementRate(r) })), 'value');
  const panel = document.getElementById('profileAnalytics');
  panel.innerHTML = `
    <div class="analytics-header">
      <h3>Profile signals</h3>
      <p>${results.length} konten dianalisis</p>
    </div>
    <div class="analytics-grid">
      <div class="analytics-metric"><strong>${pct(avgEngagement)}</strong><span>Rata-rata engagement rate</span></div>
      <div class="analytics-metric"><strong>${fmt(avg(results, 'views'))}</strong><span>Rata-rata views</span></div>
      <div class="analytics-metric"><strong>${fmt(avg(results, 'likes'))}</strong><span>Rata-rata likes</span></div>
      <div class="analytics-metric"><strong>${fmt(avg(results, 'comments'))}</strong><span>Rata-rata komentar</span></div>
      <div class="analytics-metric"><strong>${fmt(avg(results, 'shares'))}</strong><span>Rata-rata share</span></div>
      <div class="analytics-metric"><strong>${fmt(sponsored.length)}</strong><span>Konten sponsor terdeteksi</span></div>
    </div>
    <div style="height:16px"></div>
    <div class="analytics-header">
      <h3>Pemisahan konten</h3>
      <p>Organik vs sponsor</p>
    </div>
    <div class="analytics-split">
      <div class="analytics-split-row">
        <div><strong>Organik</strong><span>${organic.length} konten</span></div>
        <div style="text-align:right"><strong>${pct(avg(organic.map(r => ({ value: engagementRate(r) })), 'value'))}</strong><span>rata-rata ER</span></div>
      </div>
      <div class="analytics-split-row">
        <div><strong>Sponsored</strong><span>${sponsored.length} konten</span></div>
        <div style="text-align:right"><strong>${pct(avg(sponsored.map(r => ({ value: engagementRate(r) })), 'value'))}</strong><span>rata-rata ER</span></div>
      </div>
    </div>
  `;
}

function applyProfileFilters() {
  const query = document.getElementById('profileFeedSearch').value.trim().toLowerCase();
  const sponsoredFilter = document.getElementById('profileSponsoredFilter').value;
  const filtered = profileResultsState.filter(result => {
    const haystack = `${result.title || ''} ${result.description || ''} ${result.caption || ''} ${result.hook || ''} ${result.content || ''} ${result.transcript || ''}`.toLowerCase();
    const sponsored = isSponsoredPost(result);
    const matchesQuery = !query || haystack.includes(query);
    const matchesSponsored =
      sponsoredFilter === 'all' ||
      (sponsoredFilter === 'sponsored' && sponsored) ||
      (sponsoredFilter === 'organic' && !sponsored);
    return matchesQuery && matchesSponsored;
  });
  document.getElementById('profileFilterSummary').textContent =
    `${filtered.length} dari ${profileResultsState.length} post tampil. Gunakan rail kanan buat benchmark cepat sebelum buka video satu-satu.`;
  renderProfileCards('profileResults', filtered);
}

/* ========== SEARCH ========== */
async function doSearch() {
  const raw = document.getElementById('keywords').value.trim();
  if (!raw) return;
  const platforms = [...document.querySelectorAll('#page-search .chip.active')].map(c => c.dataset.platform);
  if (!platforms.length) { alert('Select at least one platform'); return; }

  const btn = document.getElementById('searchBtn');
  btn.disabled = true; btn.textContent = 'Lagi cari...';

  const params = new URLSearchParams({
    q: raw,
    platforms: platforms.join(','),
    max: document.getElementById('maxResults').value || 5,
    sort: document.getElementById('sortBy').value,
    date_range: document.getElementById('searchDateRange').value,
  });
  const minV = document.getElementById('minViews').value;
  const maxV = document.getElementById('maxViews').value;
  const minL = document.getElementById('minLikes').value;
  const maxL = document.getElementById('maxLikes').value;
  if (minV) params.set('min_views', minV);
  if (maxV) params.set('max_views', maxV);
  if (minL) params.set('min_likes', minL);
  if (maxL) params.set('max_likes', maxL);

  showStatus('searchStatus', 'Lagi ngumpulin sinyal dari ' + platforms.join(', ') + '...');
  document.getElementById('searchStats').innerHTML = '';
  document.getElementById('searchDownloads').innerHTML = '';
  document.getElementById('searchResults').innerHTML = '';
  setBanner('searchBanner');

  try {
    const resp = await fetch('/api/search?' + params);
    const data = await resp.json();
    if (!resp.ok || data.error) {
      hideStatus('searchStatus', 'Error: ' + handleApiFailure(data, 'Search gagal dijalankan.', 'searchBanner'));
      if (resp.status === 401) setTimeout(() => window.location.href = '/signin', 700);
      return;
    }
    hideStatus('searchStatus', `Keluar ${data.total} hasil dalam ${data.elapsed}`);
    renderStats('searchStats', data.results);
    renderDownloads('searchDownloads', data.json_file, data.csv_file);
    renderCards('searchResults', data.results);
    await refreshAccountState();
  } catch(e) {
    hideStatus('searchStatus', 'Error: ' + e.message);
  } finally {
    btn.disabled = false; btn.textContent = 'Cari Sinyal Sosial';
  }
}

/* ========== PROFILE ========== */
async function doProfile() {
  const username = document.getElementById('profileUsername').value.trim().replace(/^@+/, '');
  if (!username) return;

  const btn = document.getElementById('profileBtn');
  btn.disabled = true; btn.textContent = 'Lagi buka...';
  showStatus('profileStatus', `Lagi buka ${username}...`);
  document.getElementById('profileStats').innerHTML = '';
  document.getElementById('profileDownloads').innerHTML = '';
  document.getElementById('profileResults').innerHTML = '';
  document.getElementById('profileFeedSearch').value = '';
  document.getElementById('profileSponsoredFilter').value = 'all';
  setBanner('profileBanner');

  try {
    const params = new URLSearchParams({
      username,
      max: document.getElementById('profileMax').value || 10,
      sort: document.getElementById('profileSort').value,
      date_range: document.getElementById('profileDateRange').value,
    });
    const resp = await fetch('/api/profile?' + params);
    const data = await resp.json();
    if (!resp.ok || data.error) {
      hideStatus('profileStatus', 'Error: ' + handleApiFailure(data, 'Profile gagal dibuka.', 'profileBanner'));
      if (resp.status === 401) setTimeout(() => window.location.href = '/signin', 700);
      return;
    }
    hideStatus('profileStatus', `Ketemu ${data.total} video dalam ${data.elapsed}`);
    renderStats('profileStats', data.results);
    renderDownloads('profileDownloads', data.json_file, data.csv_file);
    profileResultsState = data.results || [];
    renderProfileAnalytics(profileResultsState);
    applyProfileFilters();
    await refreshAccountState();
  } catch(e) {
    hideStatus('profileStatus', 'Error: ' + e.message);
  } finally {
    btn.disabled = false; btn.textContent = 'Buka Profil';
  }
}

/* ========== COMMENTS ========== */
async function doComments() {
  const url = document.getElementById('commentUrl').value.trim();
  if (!url) return;

  const btn = document.getElementById('commentBtn');
  btn.disabled = true; btn.textContent = 'Lagi ambil...';
  showStatus('commentStatus', 'Lagi ekstrak komentar...');
  document.getElementById('commentResults').innerHTML = '';
  setBanner('commentBanner');

  try {
    const params = new URLSearchParams({
      url,
      max: document.getElementById('commentMax').value || 50,
    });
    const resp = await fetch('/api/comments?' + params);
    const data = await resp.json();
    if (!resp.ok || data.error) {
      hideStatus('commentStatus', 'Error: ' + handleApiFailure(data, 'Komentar gagal diambil.', 'commentBanner'));
      if (resp.status === 401) setTimeout(() => window.location.href = '/signin', 700);
      return;
    }
    const totalOnVideo = data.video_comment_count;
    const statusText = totalOnVideo != null
      ? `Terekstrak ${data.total} komentar dari ${totalOnVideo} komentar yang terdeteksi di video`
      : `Ketemu ${data.total} komentar`;
    hideStatus('commentStatus', statusText);

    document.getElementById('commentResults').innerHTML = data.comments.map(c => `
      <div class="comment-card">
        <div class="comment-user">@${c.user || c.nickname || 'anonymous'}</div>
        <div class="comment-text">${c.text}</div>
        <div class="comment-meta">${c.likes ? c.likes + ' likes' : ''} ${c.replies ? '&middot; ' + c.replies + ' replies' : ''}</div>
      </div>
    `).join('') || `<p style="color:var(--text-muted);padding:20px">${totalOnVideo ? `Video ini terdeteksi punya ${totalOnVideo} komentar, tapi TikTok belum memuat isi komentarnya ke page payload saat scrape berjalan.` : 'No comments found in page data. TikTok may load comments dynamically after scrolling.'}</p>`;
    await refreshAccountState();
  } catch(e) {
    hideStatus('commentStatus', 'Error: ' + e.message);
  } finally {
    btn.disabled = false; btn.textContent = 'Ambil Komentar';
  }
}

refreshAccountState();
</script>
</body>
</html>"""


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
