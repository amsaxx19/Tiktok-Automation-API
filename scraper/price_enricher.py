"""
TikTok Shop Price Enrichment Module

TikTok intentionally strips price data from web API responses (price=0, market_price=0).
This module enriches TikTokProduct objects with real price/shop data from external sources:

1. Tokopedia product page (via Playwright with stealth settings)
2. Google search snippet extraction
3. Video description price hints (regex-based)

Usage:
    enricher = PriceEnricher(proxy_url="http://user:pass@host:port")
    await enricher.enrich(product)  # modifies product in-place
"""

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Optional

import httpx

from scraper.kalodata import KalodataScraper


# ── Price regex patterns ──────────────────────────────────────

# "Rp 49.000", "Rp49.000", "Rp 49,000", "Rp49000"
_IDR_PRICE_RE = re.compile(
    r'Rp\.?\s?(\d{4,9}|\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?)',
    re.IGNORECASE,
)

# "49rb", "49 rb", "45ribu", "45 ribu"  (Indonesian shorthand for thousands)
_IDR_RB_RE = re.compile(
    r'(\d{1,5})\s?(?:rb|ribu)',
    re.IGNORECASE,
)

# "49k", "49K"
_IDR_K_RE = re.compile(
    r'(\d{1,5})\s?[kK](?:\b|$)',
)

# Sold count patterns: "10rb+ terjual", "1.2rb terjual", "100+ terjual"
_SOLD_RE = re.compile(
    r'([\d.,]+)\s*(?:rb\+?|ribu\+?|k\+?|jt\+?)?\s*(?:terjual|sold)',
    re.IGNORECASE,
)


def parse_idr_price(text: str) -> int:
    """Parse Indonesian price string → integer IDR.

    Examples:
        "Rp 49.000" → 49000
        "Rp49,000" → 49000
        "45rb" → 45000
        "45ribu" → 45000
        "50k" → 50000
        "Rp 1.250.000" → 1250000
    """
    # Try Rp pattern first
    m = _IDR_PRICE_RE.search(text)
    if m:
        num_str = m.group(1)
        # Remove thousand separators (. or ,)
        # Determine if last separator is decimal or thousand
        cleaned = re.sub(r'[.,]', '', num_str)
        try:
            return int(cleaned)
        except ValueError:
            pass

    # Try "rb" / "ribu" pattern
    m = _IDR_RB_RE.search(text)
    if m:
        return int(m.group(1)) * 1000

    # Try "k" pattern
    m = _IDR_K_RE.search(text)
    if m:
        return int(m.group(1)) * 1000

    # Try bare number ≥ 1000
    m = re.search(r'(\d{4,9})', text)
    if m:
        val = int(m.group(1))
        if 1000 <= val <= 100_000_000:
            return val

    return 0


def parse_sold_count(text: str) -> str:
    """Extract sold count from Indonesian text like '10rb+ terjual'."""
    m = _SOLD_RE.search(text)
    if m:
        return m.group(0).strip()
    return ""


def estimate_price_from_description(desc: str) -> int:
    """Estimate price from video description text.

    Indonesian affiliate creators often mention prices:
    - "cuma 45rb"
    - "harga Rp 49.000"
    - "diskon jadi 35ribu"
    - "only 50k"
    """
    if not desc:
        return 0

    # Look for price patterns with context keywords (order matters — most specific first)
    price_contexts = [
        # "harga Rp 49.000" style
        r'(?:harga|cuma|murah|diskon|promo|hanya|price|only)\s*Rp\.?\s?(\d{1,3}(?:[.,]\d{3})*)',
        # "cuma 45rb" / "hanya 35ribu" style
        r'(?:harga|cuma|murah|diskon|promo|hanya|price|only)\s*(\d{1,5})\s*(?:rb|ribu|k)',
        # Standalone "Rp 49.000"
        r'Rp\.?\s?(\d{1,3}(?:[.,]\d{3})*)',
        # Standalone "45rb" / "45ribu"
        r'(\d{1,5})\s*(?:rb|ribu)',
    ]

    for pattern in price_contexts:
        m = re.search(pattern, desc, re.IGNORECASE)
        if m:
            # Use the full match for parse_idr_price
            full_match = m.group(0)
            price = parse_idr_price(full_match)
            if price > 0:
                return price
            # If parse_idr_price failed, try the captured group directly
            captured = m.group(1)
            if captured:
                cleaned = re.sub(r'[.,]', '', captured)
                try:
                    val = int(cleaned)
                    # Check if it was "rb" pattern — need to multiply by 1000
                    if 'rb' in full_match.lower() or 'ribu' in full_match.lower():
                        return val * 1000
                    if val >= 1000:
                        return val
                except ValueError:
                    pass

    return 0


# ── Tokopedia scraper ─────────────────────────────────────────

async def scrape_tokopedia_price(
    product_url: str,
    proxy_url: str | None = None,
    timeout_ms: int = 30000,
) -> dict:
    """
    Scrape price, shop name, sold count from a Tokopedia product page.

    Uses Playwright with stealth settings to bypass basic bot detection.
    Returns dict with keys: price, original_price, shop_name, sold_count, rating, review_count.
    """
    result = {
        "price": 0,
        "original_price": 0,
        "shop_name": "",
        "sold_count": "",
        "rating": 0.0,
        "review_count": 0,
        "source": "tokopedia",
    }

    # Skip non-Tokopedia URLs
    if "tokopedia" not in product_url.lower():
        return result

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return result

    proxy_config = _parse_proxy(proxy_url)

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                proxy=proxy_config,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--no-sandbox',
                ],
            )
            context = await browser.new_context(
                locale='id-ID',
                timezone_id='Asia/Jakarta',
                viewport={'width': 1366, 'height': 768},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                extra_http_headers={
                    'Accept-Language': 'id-ID,id;q=0.9,en;q=0.5',
                },
            )

            # Stealth: remove webdriver flag
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                delete navigator.__proto__.webdriver;
            """)

            page = await context.new_page()

            try:
                await page.goto(product_url, wait_until='domcontentloaded', timeout=timeout_ms)
                await page.wait_for_timeout(3000)

                title = await page.title()
                if "security" in title.lower():
                    # Blocked by CAPTCHA — return empty
                    await browser.close()
                    return result

                # Extract from page
                data = await page.evaluate("""() => {
                    const result = {};

                    // Price — Tokopedia uses data-testid or specific class names
                    const priceEl = document.querySelector('[data-testid="lblPDPDetailProductPrice"]')
                        || document.querySelector('[class*="price"]')
                        || document.querySelector('h3[data-testid="lblPDPDetailProductPrice"]');
                    if (priceEl) result.price_text = priceEl.textContent;

                    // Original price (strikethrough)
                    const origEl = document.querySelector('[data-testid="lblPDPDetailOriginalPrice"]')
                        || document.querySelector('[class*="original-price"]');
                    if (origEl) result.original_price_text = origEl.textContent;

                    // Shop name
                    const shopEl = document.querySelector('[data-testid="llbPDPFooterShopName"]')
                        || document.querySelector('a[data-testid="llbPDPFooterShopName"]')
                        || document.querySelector('[class*="shop-name"]');
                    if (shopEl) result.shop_name = shopEl.textContent.trim();

                    // Sold count
                    const soldEl = document.querySelector('[data-testid="lblPDPDetailProductSoldCount"]');
                    if (soldEl) result.sold_text = soldEl.textContent;

                    // Rating
                    const ratingEl = document.querySelector('[data-testid="lblPDPDetailProductRatingNumber"]');
                    if (ratingEl) result.rating_text = ratingEl.textContent;

                    // Review count
                    const reviewEl = document.querySelector('[data-testid="lblPDPDetailProductRatingCount"]');
                    if (reviewEl) result.review_text = reviewEl.textContent;

                    // Fallback: scan all visible text for price
                    result.body_text = document.body.innerText.substring(0, 5000);

                    // JSON-LD
                    const ldScripts = document.querySelectorAll('script[type="application/ld+json"]');
                    ldScripts.forEach(s => {
                        try {
                            const d = JSON.parse(s.textContent);
                            if (d && (d['@type'] === 'Product' || d.offers)) {
                                result.jsonld = d;
                            }
                        } catch(e) {}
                    });

                    return result;
                }""")

                # Parse extracted data
                if data.get("price_text"):
                    result["price"] = parse_idr_price(data["price_text"])
                if data.get("original_price_text"):
                    result["original_price"] = parse_idr_price(data["original_price_text"])
                if data.get("shop_name"):
                    result["shop_name"] = data["shop_name"]
                if data.get("sold_text"):
                    result["sold_count"] = data["sold_text"].strip()
                if data.get("rating_text"):
                    try:
                        result["rating"] = float(data["rating_text"])
                    except ValueError:
                        pass
                if data.get("review_text"):
                    review_match = re.search(r'(\d+)', data["review_text"])
                    if review_match:
                        result["review_count"] = int(review_match.group(1))

                # JSON-LD fallback
                jsonld = data.get("jsonld", {})
                if jsonld:
                    if not result["price"]:
                        offers = jsonld.get("offers", {})
                        if isinstance(offers, list):
                            offers = offers[0] if offers else {}
                        price_val = offers.get("price") or offers.get("lowPrice", 0)
                        if price_val:
                            result["price"] = int(float(price_val))

                # Body text fallback
                if not result["price"] and data.get("body_text"):
                    prices = _IDR_PRICE_RE.findall(data["body_text"])
                    if prices:
                        # Take the first reasonable price
                        for p_str in prices:
                            val = parse_idr_price(f"Rp{p_str}")
                            if 1000 <= val <= 100_000_000:
                                result["price"] = val
                                break

            except Exception as e:
                print(f"[PriceEnricher] Tokopedia page error: {e}")

            await browser.close()

    except Exception as e:
        print(f"[PriceEnricher] Tokopedia browser error: {e}")

    return result


# ── PriceEnricher class ───────────────────────────────────────

class PriceEnricher:
    """Enrich TikTokProduct objects with real price data from external sources."""

    def __init__(self, proxy_url: str | None = None):
        self.proxy_url = proxy_url or os.getenv("PROXY_URL", "") or None
        self._cache: dict[str, dict] = {}
        self._kalodata = KalodataScraper()

    async def enrich(self, product, video_desc: str = "") -> bool:
        """
        Enrich a TikTokProduct with price/shop/sold data.

        Tries sources in order:
        0. Kalodata SSR (always works, gets real price + shop name)
        1. Video description price hints
        2. Product title price estimation
        3. Tokopedia via ScrapeOps (if API key set)
        4. Direct Tokopedia Playwright (usually blocked)
        5. Sold count from description

        Returns True if any data was enriched.
        """
        enriched = False
        product_url = getattr(product, 'product_url', '') or ''

        # Check cache
        cache_key = getattr(product, 'product_id', product_url)
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            return self._apply_enrichment(product, cached)

        # Source 0: Kalodata (highest priority — gets real price + shop + revenue)
        if cache_key:
            try:
                kdata = await self._kalodata.get_product(cache_key)
                if kdata:
                    if kdata.price_idr > 0 and product.price <= 0:
                        product.price = kdata.price_idr
                        enriched = True
                        print(f"[PriceEnricher] ✅ Kalodata price: Rp{product.price:,}")
                    if kdata.price_max_idr > kdata.price_min_idr:
                        product.original_price = kdata.price_max_idr
                    if kdata.shop_name and not product.shop_name:
                        product.shop_name = kdata.shop_name
                        enriched = True
                    if kdata.category and not product.category:
                        product.category = kdata.category
                        enriched = True
                    if kdata.ship_from and not getattr(product, 'ship_from', ''):
                        product.ship_from = kdata.ship_from
                        enriched = True
                    # Authenticated data: revenue, sold count
                    if kdata.items_sold and not getattr(product, 'items_sold_count', 0):
                        product.items_sold_count = kdata.items_sold
                        if not product.sold_count:
                            product.sold_count = f"{kdata.items_sold:,}"
                        enriched = True
                        print(f"[PriceEnricher] ✅ Kalodata sold: {kdata.items_sold:,}")
                    if kdata.revenue_idr and not getattr(product, 'revenue', 0):
                        product.revenue = kdata.revenue_idr
                        enriched = True
                        print(f"[PriceEnricher] ✅ Kalodata revenue: Rp{kdata.revenue_idr:,}")
                    if kdata.seller_type:
                        product.seller_type = kdata.seller_type
                        enriched = True
                    # Cache the kalodata result
                    self._cache[cache_key] = {
                        "price": product.price,
                        "original_price": product.original_price,
                        "shop_name": getattr(product, 'shop_name', ''),
                        "category": getattr(product, 'category', ''),
                        "sold_count": getattr(product, 'sold_count', ''),
                        "items_sold_count": getattr(product, 'items_sold_count', 0),
                        "revenue": getattr(product, 'revenue', 0),
                        "seller_type": getattr(product, 'seller_type', ''),
                        "ship_from": getattr(product, 'ship_from', ''),
                        "source": "kalodata",
                    }
                    if enriched:
                        return True
            except Exception as e:
                print(f"[PriceEnricher] Kalodata error: {e}")

        # Source 1: Video description (fast, no external requests)
        if product.price <= 0 and video_desc:
            price = estimate_price_from_description(video_desc)
            if price > 0:
                product.price = price
                enriched = True
                print(f"[PriceEnricher] ✅ Video desc estimate: Rp{price:,}")

        # Source 2: Product title price hints
        if product.price <= 0 and product.name:
            price = estimate_price_from_description(product.name)
            if price > 0:
                product.price = price
                enriched = True
                print(f"[PriceEnricher] ✅ Title estimate: Rp{price:,}")

        # Source 3: Tokopedia (only if SCRAPEOPS key available for reliable access)
        scrapeops_key = os.getenv("SCRAPEOPS_API_KEY", "").strip()
        if (
            product.price <= 0
            and "tokopedia" in product_url.lower()
            and scrapeops_key
        ):
            print(f"[PriceEnricher] Trying Tokopedia via ScrapeOps for {product.product_id}...")
            data = await self._scrape_tokopedia_via_scrapeops(
                product_url, scrapeops_key
            )
            if data.get("price", 0) > 0:
                self._cache[cache_key] = data
                enriched = self._apply_enrichment(product, data)
                if enriched:
                    print(f"[PriceEnricher] ✅ Tokopedia: Rp{product.price:,} | {product.shop_name}")
                    return True

        # Source 4: Direct Tokopedia Playwright (usually blocked by CAPTCHA)
        if (
            product.price <= 0
            and "tokopedia" in product_url.lower()
            and not scrapeops_key  # only try direct if no ScrapeOps
        ):
            print(f"[PriceEnricher] Trying Tokopedia for {product.product_id}...")
            data = await scrape_tokopedia_price(product_url, self.proxy_url)
            if data.get("price", 0) > 0:
                self._cache[cache_key] = data
                enriched = self._apply_enrichment(product, data)
                if enriched:
                    print(f"[PriceEnricher] ✅ Tokopedia: Rp{product.price:,} | {product.shop_name}")
                    return True

        # Source 5: Sold count from description
        if not product.sold_count and video_desc:
            sold = parse_sold_count(video_desc)
            if sold:
                product.sold_count = sold
                enriched = True

        if enriched:
            self._cache[cache_key] = {
                "price": product.price,
                "original_price": getattr(product, 'original_price', 0),
                "shop_name": product.shop_name,
                "sold_count": product.sold_count,
                "source": "enriched",
            }

        return enriched

    async def enrich_batch(self, products: list, video_desc: str = "") -> int:
        """Enrich a batch of products. Returns count of enriched items."""
        count = 0
        for product in products:
            try:
                if await self.enrich(product, video_desc):
                    count += 1
            except Exception as e:
                print(f"[PriceEnricher] Error enriching {getattr(product, 'product_id', '?')}: {e}")
        return count

    async def _scrape_tokopedia_via_scrapeops(
        self, product_url: str, api_key: str
    ) -> dict:
        """Use ScrapeOps proxy to access Tokopedia without CAPTCHA."""
        result = {
            "price": 0, "original_price": 0, "shop_name": "",
            "sold_count": "", "source": "scrapeops_tokopedia",
        }
        try:
            wrapped_url = (
                f"https://proxy.scrapeops.io/v1/"
                f"?api_key={api_key}"
                f"&url={product_url}"
                f"&country=id"
                f"&residential=true"
            )
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                resp = await client.get(wrapped_url)
                if resp.status_code != 200:
                    return result
                html = resp.text

                # Skip security check pages
                if "security check" in html.lower()[:500]:
                    return result

                # Extract price from JSON-LD
                ld_match = re.search(
                    r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
                    html, re.DOTALL | re.IGNORECASE,
                )
                if ld_match:
                    try:
                        ld = json.loads(ld_match.group(1))
                        offers = ld.get("offers", {})
                        if isinstance(offers, list):
                            offers = offers[0] if offers else {}
                        price_val = offers.get("price") or offers.get("lowPrice", 0)
                        if price_val:
                            result["price"] = int(float(price_val))
                    except (json.JSONDecodeError, ValueError):
                        pass

                # Fallback: find Rp prices in HTML
                if not result["price"]:
                    rp_matches = _IDR_PRICE_RE.findall(html)
                    for p_str in rp_matches:
                        val = parse_idr_price(f"Rp{p_str}")
                        if 1000 <= val <= 100_000_000:
                            result["price"] = val
                            break

                # Extract shop name
                shop_match = re.search(
                    r'data-testid="llbPDPFooterShopName"[^>]*>([^<]+)',
                    html, re.IGNORECASE,
                )
                if shop_match:
                    result["shop_name"] = shop_match.group(1).strip()

        except Exception as e:
            print(f"[PriceEnricher] ScrapeOps error: {e}")
        return result

    @staticmethod
    def _apply_enrichment(product, data: dict) -> bool:
        """Apply enrichment data to a product object."""
        changed = False

        if data.get("price", 0) > 0 and product.price <= 0:
            product.price = data["price"]
            changed = True
        if data.get("original_price", 0) > 0 and not getattr(product, 'original_price', 0):
            product.original_price = data["original_price"]
            changed = True
        if data.get("shop_name") and not product.shop_name:
            product.shop_name = data["shop_name"]
            changed = True
        if data.get("sold_count") and not product.sold_count:
            product.sold_count = data["sold_count"]
            changed = True
        if data.get("rating", 0) > 0 and not getattr(product, 'rating', 0):
            product.rating = data["rating"]
            changed = True
        if data.get("review_count", 0) > 0 and not getattr(product, 'review_count', 0):
            product.review_count = data["review_count"]
            changed = True
        if data.get("revenue", 0) > 0 and not getattr(product, 'revenue', 0):
            product.revenue = data["revenue"]
            changed = True
        if data.get("seller_type") and not getattr(product, 'seller_type', ''):
            product.seller_type = data["seller_type"]
            changed = True
        if data.get("ship_from") and not getattr(product, 'ship_from', ''):
            product.ship_from = data["ship_from"]
            changed = True
        if data.get("items_sold_count", 0) > 0 and not getattr(product, 'items_sold_count', 0):
            product.items_sold_count = data["items_sold_count"]
            changed = True
        if data.get("category") and not getattr(product, 'category', ''):
            product.category = data["category"]
            changed = True

        # Recalculate discount
        if (
            getattr(product, 'original_price', 0) > 0
            and product.price > 0
            and product.price < product.original_price
        ):
            product.discount_pct = int(
                (1 - product.price / product.original_price) * 100
            )

        return changed


def _parse_proxy(proxy_url: str | None) -> dict | None:
    """Convert proxy URL to Playwright format."""
    if not proxy_url:
        return None
    match = re.match(r'^(https?://)([^:]+):([^@]+)@([^:]+):(\d+)$', proxy_url)
    if not match:
        return None
    return {
        'server': f"{match.group(1)}{match.group(4)}:{match.group(5)}",
        'username': match.group(2),
        'password': match.group(3),
    }
