"""
TikTok Shop product scraper — detects and scrapes product data from
TikTok videos with "keranjang kuning" (yellow basket / affiliate products).

Approach: headless browser + httpx, NO official TikTok API.
Requires Indonesian residential proxy for geo-restricted TikTok Shop content.

NOTE: TikTok intentionally strips price data (price=0) from all web API
responses. The PriceEnricher module attempts to recover prices from
Tokopedia product pages and video description text.
"""

import asyncio
import json
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Optional

import httpx

from scraper.price_enricher import PriceEnricher

try:
    import h2  # noqa: F401
    _HTTP2_OK = True
except ImportError:
    _HTTP2_OK = False


# ── Data model ────────────────────────────────────────────────

@dataclass
class TikTokProduct:
    """A product found in a TikTok video's keranjang kuning."""

    product_id: str
    product_url: str
    name: str = ""
    price: int = 0  # IDR
    original_price: int = 0
    discount_pct: int = 0
    sold_count: str = ""  # "10rb+ terjual" or numeric string
    rating: float = 0.0
    review_count: int = 0
    shop_name: str = ""
    shop_url: str = ""
    thumbnail: str = ""
    commission_rate: str = ""
    category: str = ""
    video_url: str = ""  # source video that links this product
    # Kalodata-enriched fields
    revenue: int = 0  # Total revenue in IDR (last 30 days)
    seller_type: str = ""  # RETAILER, BRAND, etc.
    ship_from: str = ""  # Local, Cross-border
    items_sold_count: int = 0  # Numeric sold count from Kalodata

    def to_dict(self) -> dict:
        d = asdict(self)
        # Include extra metadata if available (set by reflow API parser)
        for attr in ("_seller_id", "_skus", "_play_count",
                      "_digg_count", "_comment_count", "_share_count"):
            if hasattr(self, attr):
                d[attr.lstrip("_")] = getattr(self, attr)
        return d


# ── Regex helpers ─────────────────────────────────────────────

# Matches product IDs in TikTok Shop URLs
_PRODUCT_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:shop\.tiktok\.com/view/product|"
    r"tiktok\.com/view/product|"
    r"tokopedia\.link|"
    r"vt\.tiktok\.com)/[^\s\"'<>]*?(\d{10,})",
)

_EXTERNAL_PRODUCT_LINK_RE = re.compile(
    r"https?://(?:shop-id\.)?tokopedia\.com/pdp/[^\s\"'<>]+?/([0-9]{10,})[^\s\"'<>]*",
    re.IGNORECASE,
)

# Matches standalone large numeric IDs that are likely product IDs
_PRODUCT_ID_RE = re.compile(r"\b(\d{15,19})\b")

# Indonesian mobile user-agent
_MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Mobile Safari/537.36"
)

# Common Indonesian price patterns: Rp 49.000, Rp49000, Rp 49,000
_PRICE_RE = re.compile(r"Rp\s?[\d.,]+", re.IGNORECASE)

_DEFAULT_PROXY_COUNTRY = os.getenv("PROXY_COUNTRY", "id").strip().lower() or "id"
_SCRAPEOPS_ENDPOINT = os.getenv(
    "SCRAPEOPS_PROXY_ENDPOINT",
    "https://proxy.scrapeops.io/v1/",
).strip()


# ── Helpers ───────────────────────────────────────────────────

def _parse_idr_price(text: str) -> int:
    """Parse Indonesian price string like 'Rp 49.000' → 49000."""
    cleaned = re.sub(r"[^\d]", "", text)
    try:
        return int(cleaned)
    except ValueError:
        return 0


def _safe_json(text: str) -> dict:
    """Attempt JSON parse, return empty dict on failure."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}


def _name_from_product_url(url: str) -> str:
    """Infer a human-friendly product name from a Tokopedia PDP URL slug."""
    match = re.search(r"/pdp/([^/?#]+)/", url, re.IGNORECASE)
    if not match:
        return ""
    slug = match.group(1)
    slug = re.sub(r"[-_]+", " ", slug).strip()
    if not slug:
        return ""
    words = [word for word in slug.split() if word]
    return " ".join(words).strip().title()


# ── Main scraper class ────────────────────────────────────────

class TikTokShopScraper:
    """
    Scrapes TikTok Shop product data from videos with affiliate products.

    Usage:
        scraper = TikTokShopScraper(proxy_url="socks5://user:pass@host:port")
        products = await scraper.scrape_products_from_video("https://tiktok.com/@user/video/123")
    """

    def __init__(self, proxy_url: str | None = None):
        self.proxy_url = proxy_url or os.getenv("PROXY_URL", "") or None
        self.scrapeops_api_key = os.getenv("SCRAPEOPS_API_KEY", "").strip()
        self.proxy_country = _DEFAULT_PROXY_COUNTRY
        self._http_timeout = 20
        self._last_video_desc = ""  # cached from last reflow scrape
        self._video_unavailable = False  # set when video is deleted/private

    @property
    def proxy_mode(self) -> str:
        if self.proxy_url:
            return "upstream_proxy"
        if self.scrapeops_api_key:
            return "scrapeops_country"
        return "direct"

    # ── Public API ────────────────────────────────────────────

    async def scrape_products_from_video(self, video_url: str) -> list[TikTokProduct]:
        """Full pipeline: detect products in video → scrape each.

        Strategy (from fastest to slowest):
          1. Browser render + intercept reflow API → rich product data
          2. HTML detection of product IDs → scrape individual pages
          3. Browser DOM extraction of external links → scrape

        After collecting products, attempts price enrichment from Tokopedia
        and video description since TikTok strips price data from web APIs.
        """
        video_desc = ""  # will be populated from reflow data if available
        self._video_unavailable = False  # reset per-call state

        # ── Strategy 1: Browser + network interception (best) ──
        products = await self._scrape_via_reflow_api(video_url)
        if products:
            # Try to get video description for price hints
            video_desc = self._last_video_desc or ""
            await self._enrich_prices(products, video_desc)
            return products

        # If Strategy 1 returned [] but captured reflow data without products,
        # the video may have been deleted (statusCode 10204) or has no commerce.
        # Check via a quick flag set by _scrape_via_reflow_api.
        if self._video_unavailable:
            print(f"[TikTokShop] Video unavailable, skipping fallback strategies")
            return []

        # ── Strategy 2: HTML-based detection (with timeout guard) ──
        try:
            product_ids = await asyncio.wait_for(
                self.detect_products_in_video(video_url),
                timeout=30,
            )
        except asyncio.TimeoutError:
            print("[TikTokShop] Strategy 2 timed out (httpx hang)")
            product_ids = []
        except Exception as e:
            print(f"[TikTokShop] Strategy 2 error: {e}")
            product_ids = []

        seen_product_urls: set[str] = set()

        for pid in product_ids[:5]:  # cap at 5 products per video
            product = await self.scrape_product(pid, video_url=video_url)
            if product:
                products.append(product)
                if product.product_url:
                    seen_product_urls.add(product.product_url)

        # ── Strategy 3: Browser DOM link extraction (with timeout guard) ──
        if not products:
            try:
                external_links = await asyncio.wait_for(
                    self._extract_product_links_from_browser(video_url),
                    timeout=45,
                )
            except asyncio.TimeoutError:
                print("[TikTokShop] Strategy 3 timed out")
                external_links = []
            except Exception as e:
                print(f"[TikTokShop] Strategy 3 error: {e}")
                external_links = []

            for link in external_links[:5]:
                if link in seen_product_urls:
                    continue
                product = await self.scrape_product_from_url(link, video_url=video_url)
                if product:
                    products.append(product)
                    seen_product_urls.add(link)

        # ── Enrich prices ──
        if products:
            await self._enrich_prices(products, video_desc)

        return products

    async def _enrich_prices(
        self, products: list[TikTokProduct], video_desc: str = ""
    ) -> None:
        """Enrich products with price, revenue, sold count from Kalodata + other sources."""
        if not products:
            return

        # Always run enrichment — even if products have prices,
        # Kalodata provides revenue, sold count, seller_type, etc.
        enricher = PriceEnricher(proxy_url=self.proxy_url)
        count = await enricher.enrich_batch(products, video_desc)
        if count:
            print(f"[TikTokShop] Enrichment: {count}/{len(products)} products enriched")

    async def _scrape_via_reflow_api(self, video_url: str) -> list[TikTokProduct]:
        """
        Open video in Playwright, intercept the reflow/recommend/item_list API
        response that TikTok fires automatically, and extract rich product data
        from the nested anchor JSON.
        """
        try:
            from playwright.async_api import async_playwright
        except Exception:
            return []

        proxy = self._playwright_proxy_config()
        reflow_data: dict = {}
        video_id = self._video_id_from_url(video_url)

        async def on_response(response):
            nonlocal reflow_data
            if "/api/reflow/recommend/item_list" in response.url and response.status == 200:
                try:
                    body = await response.body()
                    reflow_data = json.loads(body)
                    print(f"[TikTokShop] ✅ Intercepted reflow API ({len(body):,}B)")
                except Exception:
                    pass

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True, proxy=proxy)
                context = await browser.new_context(
                    locale='id-ID',
                    viewport={'width': 412, 'height': 915},
                    user_agent=_MOBILE_UA,
                    extra_http_headers={'Accept-Language': 'id-ID,id;q=0.9,en;q=0.5'},
                )
                page = await context.new_page()
                page.on("response", on_response)

                try:
                    await page.goto(video_url, wait_until='domcontentloaded', timeout=60000)
                except Exception as nav_err:
                    print(f"[TikTokShop] Navigation warning: {nav_err}")
                await page.wait_for_timeout(5000)

                # Trigger reflow by clicking shop anchor (this fires the API)
                if not reflow_data:
                    for sel in ['a[href*="shop-id."]', 'a[href*="tokopedia.com/pdp/"]',
                                'a[href*="shop.tiktok.com"]', 'a[href*="/view/product/"]',
                                '[class*="EcomAnchor"]', '[class*="ecom-anchor"]',
                                '[class*="product-anchor"]', '[class*="ProductAnchor"]']:
                        try:
                            loc = page.locator(sel)
                            if await loc.count() > 0:
                                await loc.first.click(force=True, timeout=3000)
                                await page.wait_for_timeout(5000)
                                if reflow_data:
                                    break
                        except Exception:
                            pass

                # Also try scrolling to trigger lazy loads
                if not reflow_data:
                    for i in range(3):
                        await page.evaluate(f"window.scrollBy(0, {400 * (i + 1)})")
                        await page.wait_for_timeout(2000)
                        if reflow_data:
                            break

                # ── Also extract target video's own product from page scripts ──
                target_item_data = None
                try:
                    script_data = await page.evaluate("""() => {
                        try {
                            const scripts = document.querySelectorAll('script');
                            for (const s of scripts) {
                                const t = s.textContent || '';
                                // Check for __UNIVERSAL_DATA_FOR_REHYDRATION__ first
                                if (s.id === '__UNIVERSAL_DATA_FOR_REHYDRATION__' && t.length > 100) {
                                    return JSON.stringify({_rehydration: true, _data: JSON.parse(t)});
                                }
                                // Legacy format
                                if (t.includes('"videoDetail"') || t.includes('"itemInfo"')) {
                                    return t.substring(0, 200000);
                                }
                            }
                        } catch(e) {}
                        return null;
                    }""")
                    if script_data:
                        sd = json.loads(script_data)

                        # Handle rehydration format
                        if sd.get("_rehydration"):
                            scope = sd.get("_data", {}).get("__DEFAULT_SCOPE__", {})
                            video_detail = scope.get("webapp.video-detail", {})

                            # Check for deleted/unavailable video
                            status_code = video_detail.get("statusCode", 0)
                            status_msg = video_detail.get("statusMsg", "")
                            if status_code == 10204 or "doesn't exist" in status_msg:
                                print(f"[TikTokShop] ⚠️ Video unavailable: {status_msg} (code {status_code})")
                                self._video_unavailable = True

                            item = video_detail.get("itemInfo", {}).get("itemStruct", {})
                        else:
                            # Legacy format
                            item = (sd.get("videoDetail", {}).get("itemInfo", {})
                                      .get("itemStruct", {}))

                        if item:
                            anchors_raw = item.get("anchors", [])
                            if anchors_raw:
                                target_item_data = {
                                    "item_basic": {
                                        "id": item.get("id", video_id),
                                        "desc": item.get("desc", ""),
                                        "anchors": anchors_raw,
                                        "anchor_types": [int(x) for x in item.get("anchorTypes", [])],
                                    },
                                    "item_stats": item.get("stats", {}),
                                }
                                print(f"[TikTokShop] ✅ Extracted {len(anchors_raw)} anchor(s) from page script")
                            else:
                                # Video has no product anchors
                                desc = item.get("desc", "")
                                if desc:
                                    self._last_video_desc = desc
                                    print(f"[TikTokShop] Video desc: {desc[:80]}")
                except Exception as e:
                    print(f"[TikTokShop] Script extraction warning: {e}")

                # If no reflow data at all, build from target script
                if not reflow_data and target_item_data:
                    reflow_data = {"item_list": [target_item_data]}

                # Inject target item into reflow data if not already there
                if reflow_data and target_item_data:
                    existing_ids = {
                        it.get("item_basic", {}).get("id", "")
                        for it in reflow_data.get("item_list", [])
                    }
                    tid = target_item_data["item_basic"]["id"]
                    if tid and tid not in existing_ids:
                        reflow_data["item_list"].insert(0, target_item_data)

                await browser.close()
        except Exception as e:
            print(f"[TikTokShop] Browser reflow capture failed: {e}")
            return []

        if not reflow_data or not reflow_data.get("item_list"):
            return []

        return self._parse_reflow_products(reflow_data, video_url, video_id)

    def _parse_reflow_products(
        self, reflow_data: dict, video_url: str, target_video_id: str
    ) -> list[TikTokProduct]:
        """Parse the reflow/recommend/item_list response into TikTokProduct list.

        The reflow API returns ~30 related videos. We find the target video by ID
        and extract its product anchors. If the target isn't in the list (rare),
        we still return all products from the entire response.
        """
        all_products: list[TikTokProduct] = []
        target_products: list[TikTokProduct] = []
        all_descs: list[str] = []  # collect descriptions from product-bearing videos

        for item in reflow_data.get("item_list", []):
            basic = item.get("item_basic", {})
            stats = item.get("item_stats", {})
            item_vid_id = basic.get("id", "")
            is_target = str(item_vid_id) == str(target_video_id)

            # Capture video description for price estimation
            if is_target:
                self._last_video_desc = basic.get("desc", "")

            for anchor in basic.get("anchors", []):
                extra_raw = anchor.get("extra", "")
                try:
                    extra_list = json.loads(extra_raw)
                except (json.JSONDecodeError, TypeError):
                    continue

                for ea in extra_list if isinstance(extra_list, list) else []:
                    inner_raw = ea.get("extra", "{}")
                    try:
                        pd = json.loads(inner_raw) if isinstance(inner_raw, str) else inner_raw
                    except (json.JSONDecodeError, TypeError):
                        continue

                    product_id = str(pd.get("product_id", ""))
                    if not product_id or len(product_id) < 10:
                        continue

                    # Build TikTokProduct with all available fields
                    categories = pd.get("categories", [])
                    cat_name = " > ".join(
                        c.get("category_name", "") for c in categories
                    ) if categories else ""

                    seo_url = pd.get("seo_url", "")
                    product = TikTokProduct(
                        product_id=product_id,
                        product_url=seo_url or f"https://shop.tiktok.com/view/product/{product_id}",
                        name=pd.get("title", "") or ea.get("keyword", ""),
                        price=int(pd.get("price", 0) or 0),
                        original_price=int(pd.get("market_price", 0) or 0),
                        sold_count="",
                        shop_name="",
                        shop_url="",
                        thumbnail=pd.get("cover_url", ""),
                        commission_rate="",
                        category=cat_name,
                        video_url=f"https://www.tiktok.com/@/video/{item_vid_id}" if item_vid_id else video_url,
                    )

                    # Attach extra metadata as informal fields
                    product._seller_id = str(pd.get("seller_id", ""))
                    product._source = pd.get("source", "")
                    product._skus = pd.get("skus", [])
                    product._play_count = stats.get("play_count", 0)
                    product._digg_count = stats.get("digg_count", 0)
                    product._comment_count = stats.get("comment_count", 0)
                    product._share_count = stats.get("share_count", 0)

                    all_products.append(product)
                    if is_target:
                        target_products.append(product)

            # Collect description from videos that have product anchors
            desc = basic.get("desc", "")
            if desc and basic.get("anchors"):
                all_descs.append(desc)

        # Prefer target video's products; fall back to all
        result = target_products if target_products else all_products[:5]

        # Collect video descriptions for price enrichment
        if not self._last_video_desc and all_descs:
            self._last_video_desc = " | ".join(all_descs[:5])

        # Deduplicate by product_id
        seen: set[str] = set()
        unique: list[TikTokProduct] = []
        for pr in result:
            if pr.product_id not in seen:
                seen.add(pr.product_id)
                unique.append(pr)

        if unique:
            print(f"[TikTokShop] Reflow API → {len(unique)} product(s) for video {target_video_id}")
            for pr in unique:
                print(f"  → {pr.name[:60]} | {pr.category[:40]} | {pr.product_id}")
        return unique

    @staticmethod
    def _video_id_from_url(url: str) -> str:
        """Extract video ID from a TikTok video URL."""
        m = re.search(r'/video/(\d+)', url)
        return m.group(1) if m else ""

    async def detect_products_in_video(self, video_url: str) -> list[str]:
        """
        Detect product IDs linked to a TikTok video.
        Uses 3 methods in order:
          1. Parse __UNIVERSAL_DATA_FOR_REHYDRATION__ script tag
          2. Scan HTML for shop.tiktok.com product URLs
          3. Check SIGI_STATE for commerce data
        Returns deduplicated list of product ID strings.
        """
        product_ids: list[str] = []

        try:
            html = await self._fetch_page(video_url)
        except Exception as e:
            print(f"[TikTokShop] Failed to fetch video page: {e}")
            return []

        # Method 1: __UNIVERSAL_DATA_FOR_REHYDRATION__
        product_ids.extend(self._extract_from_rehydration(html, video_url))

        # Method 2: Regex scan for product URLs in full HTML
        found = _PRODUCT_URL_RE.findall(html)
        product_ids.extend(found)

        # Method 3: SIGI_STATE (older TikTok pages)
        product_ids.extend(self._extract_from_sigi(html))

        # Method 4: Look for data-product-id or product anchor attributes
        product_ids.extend(self._extract_from_attributes(html))

        # Deduplicate and filter
        seen: set[str] = set()
        unique: list[str] = []
        for pid in product_ids:
            pid = pid.strip()
            if pid and pid not in seen and len(pid) >= 10:
                seen.add(pid)
                unique.append(pid)

        print(f"[TikTokShop] Detected {len(unique)} product(s) in {video_url}")
        return unique

    async def scrape_product(
        self, product_id: str, video_url: str = ""
    ) -> TikTokProduct | None:
        """Scrape full product details from TikTok Shop product page."""

        product = TikTokProduct(
            product_id=product_id,
            product_url=f"https://shop.tiktok.com/view/product/{product_id}",
            video_url=video_url,
        )

        try:
            html = await self._fetch_page(product.product_url)
        except Exception as e:
            print(f"[TikTokShop] Failed to fetch product {product_id}: {e}")
            return None

        # Try JSON-LD structured data (most reliable)
        self._parse_jsonld(html, product)

        # Try __UNIVERSAL_DATA / hydration script
        self._parse_product_hydration(html, product)

        # Try Open Graph meta tags as last resort
        self._parse_og_meta(html, product)

        # Calculate discount
        if (
            product.original_price > 0
            and product.price > 0
            and product.price < product.original_price
        ):
            product.discount_pct = int(
                (1 - product.price / product.original_price) * 100
            )

        if product.name:
            print(
                f"[TikTokShop] Scraped: {product.name[:60]} "
                f"Rp{product.price:,} ({product.sold_count})"
            )
            return product

        print(f"[TikTokShop] No product data found for ID {product_id}")
        return None

    async def scrape_product_from_url(
        self,
        product_url: str,
        video_url: str = "",
    ) -> TikTokProduct | None:
        """Scrape a product from an external PDP URL discovered in the video DOM."""
        product_id = self._product_id_from_url(product_url)
        product = TikTokProduct(
            product_id=product_id,
            product_url=product_url,
            video_url=video_url,
            name=_name_from_product_url(product_url),
        )

        try:
            html = await self._fetch_page(product_url)
        except Exception as e:
            print(f"[TikTokShop] External PDP fetch failed for {product_url}: {e}")
            return product if product.name else None

        if "security check" in html.lower() or "verify to continue" in html.lower():
            return product if product.name else None

        self._parse_jsonld(html, product)
        self._parse_product_hydration(html, product)
        self._parse_og_meta(html, product)

        if product.name:
            return product
        return None

    async def probe_proxy(self) -> dict:
        """Return current outward IP / country info for debugging proxy setup."""
        target = "https://ipinfo.io/json"
        try:
            payload, status = await self._fetch_json(target, use_indonesia_headers=False)
            return {
                "ok": status == 200,
                "status_code": status,
                "proxy_mode": self.proxy_mode,
                "ip": payload.get("ip", ""),
                "country": payload.get("country", ""),
                "region": payload.get("region", ""),
                "city": payload.get("city", ""),
                "org": payload.get("org", ""),
            }
        except Exception as e:
            return {
                "ok": False,
                "proxy_mode": self.proxy_mode,
                "error": str(e),
            }

    async def _extract_product_links_from_browser(self, video_url: str) -> list[str]:
        """
        Render the TikTok video page in a browser and extract shop/product hrefs
        from the visible DOM. This catches keranjang-kuning links not exposed in
        the raw HTML payloads.
        """
        try:
            from playwright.async_api import async_playwright
        except Exception as e:
            print(f"[TikTokShop] Playwright unavailable: {e}")
            return []

        proxy = self._playwright_proxy_config()
        selectors = [
            'a[href*="shop-id."]',
            'a[href*="tokopedia.com/pdp/"]',
            'a[href*="/pdp/"]',
            'a[href*="shop.tiktok.com"]',
            'a[href*="/view/product/"]',
        ]
        links: list[str] = []

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True, proxy=proxy)
                context = await browser.new_context(
                    locale='id-ID',
                    viewport={'width': 412, 'height': 915},
                    user_agent=_MOBILE_UA,
                    extra_http_headers={'Accept-Language': 'id-ID,id;q=0.9,en;q=0.5'},
                )
                page = await context.new_page()
                await page.goto(video_url, wait_until='domcontentloaded', timeout=90000)
                await page.wait_for_timeout(6000)

                for selector in selectors:
                    locator = page.locator(selector)
                    count = await locator.count()
                    for index in range(min(count, 8)):
                        href = await locator.nth(index).get_attribute('href')
                        if href and href not in links:
                            links.append(href)

                await browser.close()
        except Exception as e:
            print(f"[TikTokShop] Browser DOM extraction failed: {e}")
            return []

        cleaned: list[str] = []
        for href in links:
            if not href:
                continue
            if href.startswith('/'):
                href = f'https://www.tiktok.com{href}'
            if href not in cleaned:
                cleaned.append(href)

        if cleaned:
            print(f"[TikTokShop] DOM extracted {len(cleaned)} product link(s)")
        return cleaned

    # ── Detection helpers ─────────────────────────────────────

    @staticmethod
    def detect_commerce_in_item(item: dict) -> dict:
        """
        Quick detection of commerce/affiliate data from a TikTok item dict
        (the itemStruct from __UNIVERSAL_DATA_FOR_REHYDRATION__).
        Returns a dict with detected info, empty if no commerce detected.

        Called from TikTokScraper._scrape_video() for inline detection.
        """
        signals: dict = {}

        # 1. Check anchors (product links embedded in video)
        anchors = item.get("anchors") or []
        product_anchors = []
        for anchor in anchors:
            atype = str(anchor.get("type", "")).lower()
            if "product" in atype or "commerce" in atype or "shop" in atype:
                product_anchors.append({
                    "id": anchor.get("id", ""),
                    "type": atype,
                    "keyword": anchor.get("keyword", ""),
                    "url": anchor.get("logExtra", {}).get("url", "") if isinstance(anchor.get("logExtra"), dict) else "",
                })

        if product_anchors:
            signals["anchors"] = product_anchors
            signals["product_ids"] = [
                a["id"] for a in product_anchors if a.get("id")
            ]

        # 2. Check commerceInfo
        commerce = item.get("commerceInfo") or item.get("commerce_info") or {}
        if commerce:
            signals["commerce_info"] = commerce

        # 3. Check for shopping-related poi (point of interest)
        poi = item.get("poi") or {}
        if poi and ("shop" in str(poi).lower() or "product" in str(poi).lower()):
            signals["poi"] = poi

        # 4. Text-based heuristic in description
        desc = (item.get("desc") or "").lower()
        shopping_keywords = [
            "keranjang kuning",
            "keranjang",
            "link di bio",
            "klik keranjang",
            "cek keranjang",
            "shop now",
            "beli sekarang",
            "available in cart",
            "tap keranjang",
        ]
        matched = [kw for kw in shopping_keywords if kw in desc]
        if matched:
            signals["text_signals"] = matched

        # 5. Check for diversificationLabels
        labels = item.get("diversificationLabels") or []
        commerce_labels = [
            lb for lb in labels
            if any(w in str(lb).lower() for w in ["shop", "product", "commerce", "affiliate"])
        ]
        if commerce_labels:
            signals["labels"] = commerce_labels

        return signals

    # ── Internal methods ──────────────────────────────────────

    async def _fetch_page(self, url: str) -> str:
        """Fetch page HTML via httpx with proxy and Indonesian headers."""
        headers = self._build_headers(use_indonesia_headers=True)
        request_url, client_kwargs = self._build_request(url)

        async with httpx.AsyncClient(
            timeout=self._http_timeout,
            follow_redirects=True,
            http2=_HTTP2_OK,
            **client_kwargs,
        ) as client:
            resp = await client.get(request_url, headers=headers)
            resp.raise_for_status()
            return resp.text

    async def _fetch_json(
        self,
        url: str,
        use_indonesia_headers: bool = True,
    ) -> tuple[dict, int]:
        """Fetch a JSON endpoint through the configured proxy mode."""
        headers = self._build_headers(use_indonesia_headers=use_indonesia_headers)
        request_url, client_kwargs = self._build_request(url)
        async with httpx.AsyncClient(
            timeout=self._http_timeout,
            follow_redirects=True,
            http2=_HTTP2_OK,
            **client_kwargs,
        ) as client:
            resp = await client.get(request_url, headers=headers)
            data = resp.json() if resp.content else {}
            return data, resp.status_code

    def _build_headers(self, use_indonesia_headers: bool = True) -> dict:
        headers = {
            "User-Agent": _MOBILE_UA,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,image/webp,*/*;q=0.8"
            ),
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-Mode": "navigate",
        }
        if use_indonesia_headers:
            headers["Accept-Language"] = "id-ID,id;q=0.9,en;q=0.5"
        else:
            headers["Accept-Language"] = "en-US,en;q=0.8"
        return headers

    def _build_request(self, url: str) -> tuple[str, dict]:
        """
        Build request target + httpx client kwargs.

        Modes:
        - `upstream_proxy`: use `PROXY_URL` directly with httpx `proxy=`
        - `scrapeops_country`: wrap target URL via ScrapeOps with `country=id`
        - `direct`: no proxy
        """
        if self.proxy_url:
            return url, {"proxy": self.proxy_url}

        if self.scrapeops_api_key:
            separator = "&" if "?" in _SCRAPEOPS_ENDPOINT else "?"
            wrapped = (
                f"{_SCRAPEOPS_ENDPOINT}{separator}api_key={self.scrapeops_api_key}"
                f"&url={httpx.QueryParams({'u': url})['u']}"
                f"&country={self.proxy_country}"
            )
            return wrapped, {}

        return url, {}

    def _playwright_proxy_config(self) -> dict | None:
        """Convert `PROXY_URL` into Playwright's `proxy={...}` format."""
        if not self.proxy_url:
            return None
        match = re.match(r'^(https?://)([^:]+):([^@]+)@([^:]+):(\d+)$', self.proxy_url)
        if not match:
            return None
        return {
            'server': f"{match.group(1)}{match.group(4)}:{match.group(5)}",
            'username': match.group(2),
            'password': match.group(3),
        }

    @staticmethod
    def _product_id_from_url(url: str) -> str:
        for pattern in (_EXTERNAL_PRODUCT_LINK_RE, _PRODUCT_URL_RE):
            match = pattern.search(url)
            if match:
                return match.group(1)
        fallback = re.search(r'/([0-9]{10,})(?:\?|$)', url)
        return fallback.group(1) if fallback else url[-24:]

    def _extract_from_rehydration(self, html: str, video_url: str) -> list[str]:
        """Extract product IDs from __UNIVERSAL_DATA_FOR_REHYDRATION__."""
        product_ids: list[str] = []

        match = re.search(
            r'<script[^>]*id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>'
            r"(.*?)</script>",
            html,
            re.DOTALL,
        )
        if not match:
            return product_ids

        data = _safe_json(match.group(1))
        if not data:
            return product_ids

        scope = data.get("__DEFAULT_SCOPE__", {})

        # Navigate to video detail
        video_detail = scope.get("webapp.video-detail", {})
        item = video_detail.get("itemInfo", {}).get("itemStruct", {})

        if item:
            # Use our commerce detection
            commerce = self.detect_commerce_in_item(item)
            if "product_ids" in commerce:
                product_ids.extend(commerce["product_ids"])

            # Also check desc for product URLs
            desc = item.get("desc", "")
            product_ids.extend(_PRODUCT_URL_RE.findall(desc))

        # Check other scope keys that might have product data
        for key in scope:
            if "product" in key.lower() or "shop" in key.lower() or "commerce" in key.lower():
                sub = scope[key]
                if isinstance(sub, dict):
                    # Recursively search for product IDs in this subtree
                    product_ids.extend(self._deep_find_product_ids(sub))

        return product_ids

    def _extract_from_sigi(self, html: str) -> list[str]:
        """Extract product IDs from SIGI_STATE (older TikTok pages)."""
        product_ids: list[str] = []

        match = re.search(
            r"<script[^>]*>\s*window\[?['\"]SIGI_STATE['\"]?\]?\s*=\s*({.*?})\s*;?\s*</script>",
            html,
            re.DOTALL,
        )
        if not match:
            return product_ids

        data = _safe_json(match.group(1))
        if not data:
            return product_ids

        # ItemModule contains video items
        items = data.get("ItemModule", {})
        for _vid_id, vdata in items.items():
            if isinstance(vdata, dict):
                commerce = self.detect_commerce_in_item(vdata)
                if "product_ids" in commerce:
                    product_ids.extend(commerce["product_ids"])

        return product_ids

    @staticmethod
    def _extract_from_attributes(html: str) -> list[str]:
        """Extract product IDs from HTML attributes."""
        product_ids: list[str] = []

        # data-product-id="..."
        product_ids.extend(
            re.findall(r'data-product-id=["\'](\d{10,})["\']', html)
        )

        # href containing /product/ with numeric ID
        product_ids.extend(
            re.findall(
                r'href=["\'][^"\']*?/(?:product|item)/(\d{10,})["\']',
                html,
            )
        )

        return product_ids

    def _deep_find_product_ids(self, obj, depth: int = 0) -> list[str]:
        """Recursively search a dict/list for product IDs."""
        if depth > 6:
            return []
        ids: list[str] = []

        if isinstance(obj, dict):
            for key, val in obj.items():
                key_lower = key.lower()
                if key_lower in ("product_id", "productid", "item_id", "itemid"):
                    if isinstance(val, (str, int)) and len(str(val)) >= 10:
                        ids.append(str(val))
                elif isinstance(val, (dict, list)):
                    ids.extend(self._deep_find_product_ids(val, depth + 1))
        elif isinstance(obj, list):
            for item in obj[:20]:  # limit iteration
                ids.extend(self._deep_find_product_ids(item, depth + 1))

        return ids

    # ── Product page parsing ──────────────────────────────────

    @staticmethod
    def _parse_jsonld(html: str, product: TikTokProduct) -> None:
        """Parse JSON-LD structured data from product page."""
        matches = re.findall(
            r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
            html,
            re.DOTALL,
        )
        for raw in matches:
            ld = _safe_json(raw)
            if isinstance(ld, list):
                ld = ld[0] if ld else {}
            if not isinstance(ld, dict):
                continue

            ld_type = ld.get("@type", "").lower()
            if ld_type not in ("product", "offer", "itempage", ""):
                continue

            product.name = product.name or ld.get("name", "")
            product.thumbnail = product.thumbnail or ld.get("image", "")
            product.description_text = ld.get("description", "")

            offers = ld.get("offers", {})
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            if isinstance(offers, dict):
                price_str = str(offers.get("price", "0"))
                parsed = _parse_idr_price(price_str)
                if parsed > 0:
                    product.price = product.price or parsed

            agg_rating = ld.get("aggregateRating", {})
            if isinstance(agg_rating, dict):
                try:
                    product.rating = product.rating or float(
                        agg_rating.get("ratingValue", 0)
                    )
                    product.review_count = product.review_count or int(
                        agg_rating.get("reviewCount", 0)
                    )
                except (ValueError, TypeError):
                    pass

    @staticmethod
    def _parse_product_hydration(html: str, product: TikTokProduct) -> None:
        """Parse __UNIVERSAL_DATA or similar hydration script on product pages."""
        match = re.search(
            r'<script[^>]*id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>'
            r"(.*?)</script>",
            html,
            re.DOTALL,
        )
        if not match:
            return

        data = _safe_json(match.group(1))
        scope = data.get("__DEFAULT_SCOPE__", {})

        # TikTok Shop product pages may use different scope keys
        # Try common patterns
        for key_candidate in [
            "webapp.product-detail",
            "product_detail",
            "product",
            "webapp.shop-product",
        ]:
            detail = scope.get(key_candidate, {})
            if not detail or not isinstance(detail, dict):
                continue

            product.name = product.name or detail.get("title", "") or detail.get("name", "")

            # Price — TikTok often stores in cents/minor units
            price_data = detail.get("price", {})
            if isinstance(price_data, dict):
                sale = price_data.get("sale_price") or price_data.get("salePrice") or price_data.get("price", 0)
                orig = price_data.get("original_price") or price_data.get("originalPrice", 0)
                # Convert from minor units if needed (> 1M likely already IDR)
                sale_int = int(sale) if sale else 0
                orig_int = int(orig) if orig else 0
                if sale_int > 1_000_000:
                    sale_int = sale_int // 100
                if orig_int > 1_000_000:
                    orig_int = orig_int // 100
                product.price = product.price or sale_int
                product.original_price = product.original_price or orig_int
            elif isinstance(price_data, (int, float, str)):
                product.price = product.price or _parse_idr_price(str(price_data))

            product.sold_count = product.sold_count or str(
                detail.get("sold_count", "")
                or detail.get("soldCount", "")
                or detail.get("sales", "")
            )

            seller = detail.get("seller", {}) or detail.get("shop", {})
            if isinstance(seller, dict):
                product.shop_name = product.shop_name or seller.get("name", "") or seller.get("shop_name", "")
                product.shop_url = product.shop_url or seller.get("url", "")

            product.category = product.category or str(
                detail.get("category", {}).get("name", "")
                if isinstance(detail.get("category"), dict)
                else detail.get("category", "")
            )

            # Rating
            rating_data = detail.get("rating", {}) or detail.get("review", {})
            if isinstance(rating_data, dict):
                try:
                    product.rating = product.rating or float(
                        rating_data.get("average", 0)
                        or rating_data.get("rating", 0)
                    )
                    product.review_count = product.review_count or int(
                        rating_data.get("count", 0)
                        or rating_data.get("total", 0)
                    )
                except (ValueError, TypeError):
                    pass

            product.thumbnail = product.thumbnail or str(
                detail.get("image", "") or detail.get("cover", "") or detail.get("thumbnail", "")
            )

            # Commission (may be available in affiliate-enhanced views)
            commission = detail.get("commission", {}) or detail.get("affiliate", {})
            if isinstance(commission, dict):
                rate = commission.get("rate") or commission.get("commission_rate", "")
                if rate:
                    product.commission_rate = f"{rate}%"

            break  # found product data, stop trying other keys

    @staticmethod
    def _parse_og_meta(html: str, product: TikTokProduct) -> None:
        """Parse Open Graph meta tags as last-resort product data."""
        og = {}
        for match in re.finditer(
            r'<meta\s+(?:property|name)=["\']([^"\']+)["\']\s+'
            r'content=["\']([^"\']*)["\']',
            html,
        ):
            og[match.group(1)] = match.group(2)

        product.name = product.name or og.get("og:title", "")
        product.thumbnail = product.thumbnail or og.get("og:image", "")

        # Try to extract price from description
        desc = og.get("og:description", "")
        if not product.price and desc:
            price_match = _PRICE_RE.search(desc)
            if price_match:
                product.price = _parse_idr_price(price_match.group(0))
