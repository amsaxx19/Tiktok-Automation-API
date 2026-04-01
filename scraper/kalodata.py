"""
Kalodata product data scraper.

Kalodata (kalodata.com) is a TikTok Shop analytics platform that provides:
- Product prices (in IDR/USD)
- Shop name, category, ship-from, seller type
- Revenue, sold count, commission (requires login)
- Historical sales data (requires login)

This module provides two scraping modes:
1. SSR-only (no login): Gets price, shop name, category from server-rendered HTML
2. Authenticated API (with login): Gets full metrics via direct API calls

The authenticated mode uses httpx to call Kalodata's REST API directly,
bypassing Cloudflare by:
  - Generating a deviceId cookie (FingerprintJS-like hash)
  - Setting appVersion + deviceType cookies
  - Getting a SESSION cookie from homepage
  - POSTing to /user/login with EMAIL_PASSWORD method

Usage:
    from scraper.kalodata import KalodataScraper

    ks = KalodataScraper()  # reads KALODATA_EMAIL/PASSWORD from env
    data = await ks.get_product(product_id="1732181709510707083")
"""

import asyncio
import hashlib
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

# Load .env file if available
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# ── USD to IDR conversion ────────────────────────────────────
_USD_TO_IDR = 16_300

# ── Rate-limit cooldown tracking ─────────────────────────────
_login_cooldown_until: float = 0.0

# ── Session persistence file ─────────────────────────────────
_SESSION_FILE = Path.home() / ".kalodata_session.json"


def _save_session(client: httpx.AsyncClient, device_id: str = "", ua: str = "") -> None:
    """Save session cookies + context to disk for reuse across runs."""
    try:
        cookies_dict = {}
        for cookie in client.cookies.jar:
            cookies_dict[cookie.name] = cookie.value
        session_data = {
            "cookies": cookies_dict,
            "device_id": device_id,
            "user_agent": ua,
            "saved_at": time.time(),
        }
        _SESSION_FILE.write_text(json.dumps(session_data, indent=2))
    except Exception:
        pass  # Non-critical


def _load_session() -> Optional[Dict[str, Any]]:
    """Load saved session data from disk. Returns None if expired/missing."""
    try:
        if not _SESSION_FILE.exists():
            return None
        data = json.loads(_SESSION_FILE.read_text())
        # Check age: discard sessions older than 6 hours
        saved_at = data.get("saved_at", 0)
        if time.time() - saved_at > 6 * 3600:
            _SESSION_FILE.unlink(missing_ok=True)
            return None
        if not data.get("cookies"):
            return None
        return data
    except Exception:
        return None


def _parse_idr_string(s: str) -> int:
    """Parse Kalodata's IDR formatted strings like 'Rp3.54m', 'Rp502.38k', 'Rp0.00'."""
    if not s:
        return 0
    s = s.strip()
    # Remove 'Rp' prefix
    s = re.sub(r"^Rp\s*", "", s, flags=re.IGNORECASE)
    if not s or s == "NaN":
        return 0
    try:
        multiplier = 1
        if s.endswith("m"):
            multiplier = 1_000_000
            s = s[:-1]
        elif s.endswith("k"):
            multiplier = 1_000
            s = s[:-1]
        elif s.endswith("b"):
            multiplier = 1_000_000_000
            s = s[:-1]
        # Remove commas and dots used as thousands separator
        # Kalodata uses format like "3.54m" or "502.38k" (dot = decimal)
        num = float(s.replace(",", ""))
        return int(num * multiplier)
    except (ValueError, TypeError):
        return 0


@dataclass
class KalodataProduct:
    """Parsed product data from Kalodata."""

    product_id: str = ""
    title: str = ""
    # Prices in IDR (primary) and USD (secondary)
    price_min_usd: float = 0.0
    price_max_usd: float = 0.0
    lowest_price_30d_usd: float = 0.0
    price_min_idr: int = 0
    price_max_idr: int = 0
    lowest_price_30d_idr: int = 0
    # Shop / seller
    shop_name: str = ""
    shop_id: str = ""
    seller_id: str = ""
    seller_type: str = ""  # RETAILER, etc.
    # Category
    category: str = ""  # e.g. "Hair Loss Products"
    parent_category: str = ""  # e.g. "Haircare & Styling"
    # Metadata
    ship_from: str = ""
    delivery_type: str = ""  # local, cross-border
    earliest_date: str = ""
    thumbnail: str = ""
    is_tokopedia: bool = False
    brand_name: str = ""
    # Authenticated-only fields
    revenue_idr: int = 0
    revenue_text: str = ""
    items_sold: int = 0
    items_sold_text: str = ""
    avg_unit_price_idr: int = 0
    commission_rate: float = 0.0
    rating: float = 0.0
    review_count: int = 0
    # Revenue breakdown
    video_revenue_idr: int = 0
    live_revenue_idr: int = 0
    mall_revenue_idr: int = 0
    related_creator_count: int = 0
    # Source
    source: str = "kalodata"

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v}

    @property
    def price_idr(self) -> int:
        """Best estimate of current price in IDR."""
        if self.price_min_idr:
            return self.price_min_idr
        if self.price_min_usd:
            return int(self.price_min_usd * _USD_TO_IDR)
        return 0


class KalodataScraper:
    """Scrapes product data from Kalodata.com via SSR + API.

    Two modes:
    1. SSR (Playwright) — no login, gets price/shop/category
    2. API (httpx) — with login, gets full detail + revenue + sold
    """

    BASE_URL = "https://www.kalodata.com"

    def __init__(
        self,
        email: str = "",
        password: str = "",
        usd_to_idr: int = _USD_TO_IDR,
    ):
        self.email = email or os.getenv("KALODATA_EMAIL", "")
        self.password = password or os.getenv("KALODATA_PASSWORD", "")
        self.usd_to_idr = usd_to_idr
        self._http_client: Optional[httpx.AsyncClient] = None
        self._logged_in = False
        self._login_lock = asyncio.Lock()
        self._cache: Dict[str, KalodataProduct] = {}
        self._device_id: str = ""
        self._ua: str = ""

    # ── Public API ────────────────────────────────────────────

    async def get_product(
        self, product_id: str, use_cache: bool = True
    ) -> Optional[KalodataProduct]:
        """Get product data from Kalodata.

        Strategy:
        1. If credentials available → try API first (fastest, most data)
        2. Fallback to SSR scraping (always works, less data)
        """
        if use_cache and product_id in self._cache:
            return self._cache[product_id]

        product = None

        # Strategy 1: Authenticated API (if credentials available)
        if self.email and self.password:
            try:
                product = await self._fetch_via_api(product_id)
                if product and product.title:
                    self._cache[product_id] = product
                    return product
            except Exception as e:
                print(f"[kalodata] API fetch failed: {e}")

        # Strategy 2: SSR scraping (fallback)
        try:
            product = await self._scrape_ssr(product_id)
            if product:
                self._cache[product_id] = product
        except Exception as e:
            print(f"[kalodata] SSR scrape failed: {e}")

        return product

    async def get_products_batch(
        self, product_ids: List[str]
    ) -> Dict[str, KalodataProduct]:
        """Get multiple products. Returns dict of product_id → KalodataProduct."""
        results = {}
        for pid in product_ids:
            data = await self.get_product(pid)
            if data:
                results[pid] = data
            await asyncio.sleep(1)
        return results

    # ── API Authentication ────────────────────────────────────

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Get or create an authenticated httpx client."""
        if self._http_client and self._logged_in:
            return self._http_client

        async with self._login_lock:
            # Double-check after acquiring lock
            if self._http_client and self._logged_in:
                return self._http_client

            # Try restoring saved session first (avoids login = avoids rate limit)
            saved = _load_session()
            if saved:
                saved_cookies = saved.get("cookies", {})
                saved_device_id = saved.get("device_id", "")
                saved_ua = saved.get("user_agent", "")
                jar = httpx.Cookies()
                for k, v in saved_cookies.items():
                    jar.set(k, v, domain="www.kalodata.com")
                client = httpx.AsyncClient(
                    timeout=30,
                    follow_redirects=True,
                    cookies=jar,
                )
                # Validate with a lightweight call
                try:
                    resp = await client.post(
                        self.BASE_URL + "/product/detail/access",
                        json={"product_id": "1732181709510707083", "country": "id"},
                        headers={
                            "User-Agent": saved_ua,
                            "Accept": "application/json, text/plain, */*",
                            "Content-Type": "application/json",
                            "Origin": self.BASE_URL,
                            "Referer": self.BASE_URL + "/product/detail",
                            "language": "en-US",
                            "country": "ID",
                            "currency": "IDR",
                        },
                    )
                    data = resp.json()
                    if data.get("success"):
                        self._http_client = client
                        self._logged_in = True
                        self._device_id = saved_device_id
                        self._ua = saved_ua
                        print("[kalodata] ✅ Restored saved session")
                        return client
                    else:
                        code = data.get("code", "")
                        print(f"[kalodata] Session expired (code {code}), will login fresh")
                        await client.aclose()
                        _SESSION_FILE.unlink(missing_ok=True)
                except Exception:
                    await client.aclose()
                    _SESSION_FILE.unlink(missing_ok=True)

            # Check rate-limit cooldown
            global _login_cooldown_until
            now = time.time()
            if now < _login_cooldown_until:
                wait = int(_login_cooldown_until - now)
                raise Exception(
                    f"Login rate-limited, retry in {wait}s"
                )

            # Create fresh client with required cookies
            device_id = hashlib.md5(
                str(uuid.uuid4()).encode()
            ).hexdigest()

            jar = httpx.Cookies()
            jar.set("deviceId", device_id, domain="www.kalodata.com")
            jar.set("appVersion", "2.0", domain="www.kalodata.com")
            jar.set("deviceType", "pc", domain="www.kalodata.com")

            client = httpx.AsyncClient(
                timeout=30,
                follow_redirects=True,
                cookies=jar,
            )

            ua = (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            )

            # Step 1: Get SESSION cookie from homepage
            await client.get(
                self.BASE_URL + "/",
                headers={"User-Agent": ua, "Accept": "text/html"},
            )

            # Step 2: Login via API
            login_headers = {
                "User-Agent": ua,
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json",
                "Origin": self.BASE_URL,
                "Referer": self.BASE_URL + "/",
                "language": "en-US",
                "country": "ID",
                "currency": "IDR",
            }

            login_payload = {
                "scene": "login",
                "loginMethod": "EMAIL_PASSWORD",
                "tcCode": "",
                "email": self.email,
                "emailPassword": self.password,
            }

            resp = await client.post(
                self.BASE_URL + "/user/login",
                json=login_payload,
                headers=login_headers,
            )

            data = resp.json()
            if data.get("success"):
                self._http_client = client
                self._logged_in = True
                self._device_id = device_id
                self._ua = ua
                _save_session(client, device_id=device_id, ua=ua)
                print("[kalodata] ✅ API login successful")
                return client
            else:
                code = data.get("code", "")
                msg = data.get("message", "")
                await client.aclose()

                # Handle rate limit (code 1303)
                if code == "1303":
                    # msg is like "22:48" — Beijing (UTC+8) timestamp
                    # when the lock expires. NOT a countdown.
                    # Each login attempt during lock resets timer.
                    try:
                        from datetime import (
                            datetime as dt_cls,
                            timedelta,
                            timezone,
                        )

                        parts = msg.split(":")
                        hh, mm = int(parts[0]), int(parts[1])
                        beijing_tz = timezone(timedelta(hours=8))
                        now_bj = dt_cls.now(beijing_tz)
                        target_bj = now_bj.replace(
                            hour=hh,
                            minute=mm,
                            second=0,
                            microsecond=0,
                        )
                        if target_bj < now_bj:
                            target_bj += timedelta(days=1)
                        # Add 2 min buffer
                        target_bj += timedelta(minutes=2)
                        target_utc = target_bj.timestamp()
                        _login_cooldown_until = target_utc
                        wait_min = int(
                            (target_utc - time.time()) / 60
                        )
                        print(
                            f"[kalodata] ⚠️ Rate-limited until "
                            f"{msg} Beijing (~{wait_min}min)"
                        )
                    except (ValueError, IndexError):
                        _login_cooldown_until = time.time() + 1800
                    raise Exception(f"Login rate-limited: {msg}")

                # Handle unstable network (code 1200)
                if code == "1200":
                    raise Exception(
                        "Login rejected (code 1200 — device fingerprint)"
                    )

                raise Exception(f"Login failed: {msg} (code {code})")

    def _api_headers(self) -> dict:
        """Standard headers for API calls."""
        return {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": self.BASE_URL,
            "Referer": self.BASE_URL + "/product/detail",
            "language": "en-US",
            "country": "ID",
            "currency": "IDR",
        }

    # ── API Product Fetching ──────────────────────────────────

    async def _fetch_via_api(
        self, product_id: str
    ) -> Optional[KalodataProduct]:
        """Fetch full product data via Kalodata's authenticated REST API.

        Calls:
        1. POST /product/detail/access — register access (required)
        2. POST /product/detail — full product info
        3. POST /product/detail/total — revenue/sales totals
        """
        client = await self._ensure_client()
        headers = self._api_headers()

        product = KalodataProduct(product_id=product_id, source="kalodata_api")

        # 1. Register access
        try:
            await client.post(
                self.BASE_URL + "/product/detail/access",
                json={"id": product_id},
                headers=headers,
            )
        except Exception:
            pass

        # 2. Get product detail
        resp = await client.post(
            self.BASE_URL + "/product/detail",
            json={"id": product_id},
            headers=headers,
        )
        detail_json = resp.json()

        if not detail_json.get("success"):
            msg = detail_json.get("message", "unknown")
            code = detail_json.get("code", "")
            # Session expired
            if code == "1302":
                self._logged_in = False
                self._http_client = None
                # Retry once
                client = await self._ensure_client()
                resp = await client.post(
                    self.BASE_URL + "/product/detail",
                    json={"id": product_id},
                    headers=headers,
                )
                detail_json = resp.json()
                if not detail_json.get("success"):
                    print(f"[kalodata] detail failed after re-login: {msg}")
                    return None
            else:
                print(f"[kalodata] detail failed: {msg} ({code})")
                return None

        detail = detail_json.get("data", {})
        if not detail:
            return None

        # Parse product detail
        product.title = detail.get("product_title", "")
        product.seller_id = detail.get("seller_id", "")
        product.seller_type = detail.get("seller_type", "")
        product.brand_name = detail.get("brand_name", "")
        product.delivery_type = detail.get("delivery_type", "")
        product.is_tokopedia = bool(detail.get("is_tokopedia", 0))
        product.review_count = detail.get("review_count", 0)
        product.category = detail.get("ter_cate_id", "")
        product.parent_category = detail.get("sec_cate_id", "")
        # Build full category path if multiple levels available
        pri_cat = detail.get("pri_cate_id", "")
        sec_cat = detail.get("sec_cate_id", "")
        ter_cat = detail.get("ter_cate_id", "")
        if pri_cat and sec_cat and ter_cat:
            product.category = f"{pri_cat} > {sec_cat} > {ter_cat}"
            product.parent_category = sec_cat
        elif pri_cat and sec_cat:
            product.category = f"{pri_cat} > {sec_cat}"
            product.parent_category = pri_cat
        # Shop name — try multiple field names
        product.shop_name = (
            detail.get("shop_name", "")
            or detail.get("name", "")
            or detail.get("seller_name", "")
            or detail.get("store_name", "")
        )
        # Ship from — derive from delivery_type if not explicit
        product.ship_from = detail.get("ship_from", "")
        if not product.ship_from and product.delivery_type:
            product.ship_from = (
                "Local" if "local" in product.delivery_type.lower()
                else "Cross-border"
            )

        # Parse prices (Kalodata returns IDR-formatted strings)
        min_price_str = detail.get("min_original_price", "")
        max_price_str = detail.get("max_original_price", "")
        min_real_str = detail.get("min_real_price", "")
        max_real_str = detail.get("max_real_price", "")
        unit_price_str = detail.get("unit_price", "")
        product.price_min_idr = _parse_idr_string(min_price_str)
        product.price_max_idr = _parse_idr_string(max_price_str)
        product.avg_unit_price_idr = _parse_idr_string(unit_price_str)
        # Fallbacks
        if not product.price_min_idr:
            product.price_min_idr = _parse_idr_string(min_real_str) or product.avg_unit_price_idr
        if not product.price_max_idr:
            product.price_max_idr = _parse_idr_string(max_real_str) or product.price_min_idr
        # 30-day lowest price
        lowest_30d_str = detail.get("min_in_30_price", "")
        if lowest_30d_str:
            product.lowest_price_30d_idr = _parse_idr_string(lowest_30d_str)

        # Shop rating
        shop_rating = detail.get("shop_rating", "")
        if shop_rating:
            try:
                product.rating = float(shop_rating)
            except (ValueError, TypeError):
                pass

        # Product rating (separate from shop rating)
        prod_rating = detail.get("product_rating", "")
        if prod_rating and not product.rating:
            try:
                product.rating = float(prod_rating)
            except (ValueError, TypeError):
                pass

        # Review count
        product.review_count = (
            detail.get("product_review_count", 0)
            or detail.get("review_count", 0)
        )

        # Commission rate (e.g. "8%" → 8.0)
        commission_str = detail.get("commission_rate", "")
        if commission_str:
            try:
                product.commission_rate = float(
                    str(commission_str).replace("%", "").strip()
                )
            except (ValueError, TypeError):
                pass

        # Thumbnail
        images = detail.get("images", [])
        if isinstance(images, list) and images:
            # Images are URI strings
            for img in images:
                if isinstance(img, str) and img:
                    product.thumbnail = (
                        f"https://p16-oec-sg.ibyteimg.com/{img}"
                    )
                    break
                elif isinstance(img, dict):
                    url_list = img.get("url_list", [])
                    if url_list:
                        product.thumbnail = url_list[0]
                        break

        # 3. Get total stats (last 30 days)
        try:
            from datetime import datetime, timedelta

            end_date = datetime.now().strftime("%Y-%m-%d")
            start_date = (
                datetime.now() - timedelta(days=30)
            ).strftime("%Y-%m-%d")

            total_resp = await client.post(
                self.BASE_URL + "/product/detail/total",
                json={
                    "id": product_id,
                    "startDate": start_date,
                    "endDate": end_date,
                },
                headers=headers,
            )
            total_json = total_resp.json()

            if total_json.get("success"):
                total = total_json.get("data", {})
                product.revenue_idr = _parse_idr_string(
                    total.get("revenue", "")
                )
                product.revenue_text = total.get("revenue", "")
                product.video_revenue_idr = _parse_idr_string(
                    total.get("video_revenue", "")
                )
                product.live_revenue_idr = _parse_idr_string(
                    total.get("live_revenue", "")
                )
                product.mall_revenue_idr = _parse_idr_string(
                    total.get("shopping_mall_revenue", "")
                )
                product.related_creator_count = total.get(
                    "related_creator_count", 0
                )

                # Parse sold count (format: "4.94k", "12.3k", "156")
                sale_str = total.get("sale", "0")
                product.items_sold = _parse_idr_string(sale_str)
                if not product.items_sold:
                    # Fallback: try direct int parse
                    try:
                        product.items_sold = int(
                            str(sale_str).replace(",", "")
                        )
                    except (ValueError, TypeError):
                        pass
                product.items_sold_text = str(sale_str)

                # Unit price from total
                up = total.get("unit_price", "")
                up_idr = _parse_idr_string(up)
                if up_idr and not product.avg_unit_price_idr:
                    product.avg_unit_price_idr = up_idr

        except Exception as e:
            print(f"[kalodata] total stats error: {e}")

        return product

    # ── SSR Scraping (no login, fallback) ─────────────────────

    async def _scrape_ssr(self, product_id: str) -> Optional[KalodataProduct]:
        """Extract product data from Kalodata's server-side rendered page.

        This works WITHOUT login and gets:
        - Price range (USD)
        - 30d lowest price
        - Shop name
        - Category
        - Ship from
        - Earliest date recorded
        - Product title
        """
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            ctx = await browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            )
            page = await ctx.new_page()
            await page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )

            url = (
                f"https://www.kalodata.com/product/detail"
                f"?id={product_id}&language=en-US&currency=IDR&region=ID"
            )

            try:
                await page.goto(
                    url, wait_until="domcontentloaded", timeout=20000
                )
            except Exception:
                pass

            await asyncio.sleep(3)

            # Extract data from rendered page
            raw = await page.evaluate(
                """() => {
                const body = document.body.innerText;
                const result = {};

                // Price range
                const priceRange = body.match(/\\$([\\d,.]+)\\s*-\\s*\\$([\\d,.]+)/);
                if (priceRange) {
                    result.price_min = priceRange[1];
                    result.price_max = priceRange[2];
                }

                // Single price
                if (!result.price_min) {
                    const single = body.match(/\\$([\\d,.]+)/);
                    if (single) result.price_min = single[1];
                }

                // 30d lowest
                const lowest = body.match(/30d Lowest Price[:\\s]*\\$([\\d,.]+)/);
                if (lowest) result.lowest_30d = lowest[1];

                // Category
                const cat = body.match(/Category:\\s*\\n([\\w\\s&>]+?)\\n/);
                if (cat) result.category = cat[1].trim();

                // Shop name
                const shop = body.match(/Detail\\s+([A-Z][A-Za-z0-9\\s._-]+?)\\s+The earliest/);
                if (shop) result.shop_name = shop[1].trim();

                // Earliest date
                const date = body.match(/earliest date recorded\\s+(\\d{2}\\/\\d{2}\\/\\d{4})/);
                if (date) result.earliest_date = date[1];

                // Ship from
                const ship = body.match(/Ship From\\s+(\\w+)/);
                if (ship) result.ship_from = ship[1];

                // Title from <title>
                result.title = document.title
                    .replace(' Indonesia TikTok Data', '')
                    .replace(' TikTok Data', '')
                    .trim();

                // Thumbnail
                const img = document.querySelector(
                    'img[src*="cloudfront"], img[src*="tiktok"], img[src*="upload"]'
                );
                if (img) result.thumbnail = img.src;

                // Item Sold (visible even without login as sample)
                const soldMatch = body.match(/Item Sold\\s+([\\d,.]+[kKmM]?)/);
                if (soldMatch) result.items_sold_text = soldMatch[1];

                // Revenue (visible as sample)
                const revMatch = body.match(/Revenue\\s+(Rp[\\d.,]+[kKmM]?)/);
                if (revMatch) result.revenue_text = revMatch[1];

                return result;
            }"""
            )

            await browser.close()

        if not raw:
            return None

        # Parse into KalodataProduct
        product = KalodataProduct(product_id=product_id)
        product.title = raw.get("title", "")

        # Parse prices
        price_min_str = raw.get("price_min", "")
        price_max_str = raw.get("price_max", price_min_str)
        lowest_str = raw.get("lowest_30d", "")

        def _parse_usd(s: str) -> float:
            if not s:
                return 0.0
            try:
                return float(s.replace(",", ""))
            except ValueError:
                return 0.0

        product.price_min_usd = _parse_usd(price_min_str)
        product.price_max_usd = _parse_usd(price_max_str)
        product.lowest_price_30d_usd = _parse_usd(lowest_str)

        # Convert to IDR
        if product.price_min_usd:
            product.price_min_idr = int(product.price_min_usd * self.usd_to_idr)
        if product.price_max_usd:
            product.price_max_idr = int(product.price_max_usd * self.usd_to_idr)
        if product.lowest_price_30d_usd:
            product.lowest_price_30d_idr = int(
                product.lowest_price_30d_usd * self.usd_to_idr
            )

        product.shop_name = raw.get("shop_name", "")
        product.category = raw.get("category", "")
        product.ship_from = raw.get("ship_from", "")
        product.earliest_date = raw.get("earliest_date", "")
        product.thumbnail = raw.get("thumbnail", "")

        return product

    # ── Cleanup ───────────────────────────────────────────────

    async def close(self):
        """Close the httpx client."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None
            self._logged_in = False


# ── Convenience function ──────────────────────────────────────


async def kalodata_get_price(product_id: str) -> Optional[Dict[str, Any]]:
    """Quick function to get product price from Kalodata.

    Returns dict with price_idr, shop_name, category, and more.
    """
    scraper = KalodataScraper()
    try:
        product = await scraper.get_product(product_id)
        if product:
            return product.to_dict()
        return None
    finally:
        await scraper.close()
