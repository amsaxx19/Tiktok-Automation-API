#!/usr/bin/env python3
"""
Network Capture Script — Intercept XHR/fetch requests from TikTok
video pages to discover product/shop JSON endpoints.

Opens a TikTok video in Playwright, captures all network traffic,
clicks keranjang kuning (shop anchor), and dumps every JSON response
that looks related to products/shop/commerce.

Usage:
    .venv/bin/python3 scripts/capture_shop_xhr.py <VIDEO_URL>
"""

import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# ── Setup ─────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

PROXY_URL = os.getenv("PROXY_URL", "")
MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Mobile Safari/537.36"
)

# Keywords indicating product/shop/commerce JSON
INTERESTING_URL_KEYWORDS = [
    "product", "shop", "commerce", "anchor", "item",
    "pdp", "cart", "checkout", "recommend", "reflow",
    "goods", "sku", "price", "order", "ecommerce",
    "affiliate", "oec", "/api/", "tiktokshop",
]

# ── Helpers ───────────────────────────────────────────────────

def proxy_config() -> dict | None:
    if not PROXY_URL:
        return None
    m = re.match(r'^(https?://)([^:]+):([^@]+)@([^:]+):(\d+)$', PROXY_URL)
    if not m:
        return None
    return {
        'server': f"{m.group(1)}{m.group(4)}:{m.group(5)}",
        'username': m.group(2),
        'password': m.group(3),
    }


def is_interesting(url: str) -> bool:
    """Check if URL looks related to product/shop data."""
    lower = url.lower()
    return any(kw in lower for kw in INTERESTING_URL_KEYWORDS)


def summarise_url(url: str, max_len: int = 120) -> str:
    """Short display version of URL."""
    parsed = urlparse(url)
    path = parsed.path
    if len(url) > max_len:
        return f"{parsed.scheme}://{parsed.hostname}{path[:60]}...?{parsed.query[:30]}..."
    return url


# ── Captured response store ───────────────────────────────────

class CapturedResponse:
    def __init__(self, url: str, status: int, content_type: str, body: bytes, timing: str):
        self.url = url
        self.status = status
        self.content_type = content_type
        self.body = body
        self.timing = timing
        self._json = None

    @property
    def is_json(self) -> bool:
        return "json" in self.content_type or "javascript" in self.content_type

    @property
    def json_data(self) -> dict | list | None:
        if self._json is not None:
            return self._json
        try:
            self._json = json.loads(self.body)
            return self._json
        except Exception:
            return None

    def has_product_data(self) -> bool:
        """Check if the JSON body contains product-related fields."""
        data = self.json_data
        if data is None:
            return False
        text = json.dumps(data).lower()
        product_signals = [
            "product_id", "product_name", "price", "sold_count",
            "shop_name", "commission", "rating", "sku",
            "sale_price", "original_price", "review_count",
            "item_id", "goods_id", "product_url",
        ]
        matches = [s for s in product_signals if s in text]
        return len(matches) >= 2


# ── Main capture logic ────────────────────────────────────────

async def capture_network(video_url: str, headless: bool = True):
    from playwright.async_api import async_playwright

    proxy = proxy_config()
    captured: list[CapturedResponse] = []
    t0 = time.time()

    print(f"\n{'='*80}", flush=True)
    print(f"🔍 Network Capture — {video_url}", flush=True)
    print(f"   Proxy: {proxy['server'] if proxy else 'direct'}", flush=True)
    print(f"   Headless: {headless}", flush=True)
    print(f"{'='*80}\n", flush=True)

    print("🚀 Launching browser...", flush=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless, proxy=proxy)
        print("✅ Browser launched", flush=True)
        context = await browser.new_context(
            locale='id-ID',
            viewport={'width': 412, 'height': 915},
            user_agent=MOBILE_UA,
            extra_http_headers={'Accept-Language': 'id-ID,id;q=0.9,en;q=0.5'},
        )
        print("✅ Context created", flush=True)

        # ── Response handler — capture everything interesting ──
        async def on_response(response):
            url = response.url
            if not is_interesting(url):
                return
            try:
                ct = response.headers.get("content-type", "")
                body = await response.body()
                elapsed = f"{time.time() - t0:.1f}s"
                cr = CapturedResponse(url, response.status, ct, body, elapsed)
                captured.append(cr)

                status_icon = "✅" if response.status == 200 else "⚠️"
                json_icon = "📦" if cr.is_json else "📄"
                product_icon = " 🛒 HAS PRODUCT DATA!" if cr.has_product_data() else ""
                print(f"  [{elapsed}] {status_icon} {json_icon} {response.status} {summarise_url(url)}{product_icon}", flush=True)
            except Exception as e:
                print(f"  [!] Failed to capture {url[:80]}: {e}", flush=True)

        page = await context.new_page()
        page.on("response", on_response)
        print("✅ Page created, navigating...", flush=True)

        # ── Phase 1: Load page ────────────────────────────────
        print("📡 Phase 1: Loading video page...", flush=True)
        try:
            await page.goto(video_url, wait_until='domcontentloaded', timeout=60000)
        except Exception as e:
            print(f"   ⚠️ Navigation warning: {e}", flush=True)
        await page.wait_for_timeout(4000)
        p1_count = len(captured)
        print(f"   → Captured {p1_count} interesting responses during page load\n", flush=True)

        # ── Phase 2: Click shop/product anchors ───────────────
        print("🛒 Phase 2: Looking for keranjang kuning / shop anchors...", flush=True)

        shop_selectors = [
            # TikTok product anchor / keranjang kuning
            '[data-e2e="product-anchor"]',
            '[class*="product-anchor"]',
            '[class*="ProductAnchor"]',
            'div[class*="commerce"] a',
            'div[class*="Commerce"] a',
            # Shop link patterns
            'a[href*="shop-id."]',
            'a[href*="tokopedia.com/pdp/"]',
            'a[href*="shop.tiktok.com"]',
            'a[href*="/view/product/"]',
            # Generic shopping-related
            '[class*="shopping"]',
            '[class*="Shopping"]',
            '[class*="cart"]',
            '[class*="Cart"]',
            '[class*="basket"]',
            '[class*="Basket"]',
            # Bottom bar anchor (mobile view)
            '[class*="anchor-container"]',
            '[class*="AnchorContainer"]',
            '[class*="product-card"]',
            '[class*="ProductCard"]',
            # TikTok's product popup trigger
            '[class*="tiktok-shop"]',
            '[class*="TikTokShop"]',
        ]

        clicked = False
        for selector in shop_selectors:
            try:
                locator = page.locator(selector)
                count = await locator.count()
                if count > 0:
                    el = locator.first
                    visible = await el.is_visible()
                    text = (await el.text_content() or "").strip()[:60]
                    tag = await el.evaluate("el => el.tagName")
                    href = await el.evaluate("el => el.href || el.getAttribute('href') || ''")
                    print(f"   ✅ Found: {selector} ({count}x) — <{tag}> '{text}' href={href[:80]}", flush=True)

                    # Click it
                    print(f"   🖱️ Clicking...", flush=True)
                    await el.click(timeout=5000)
                    clicked = True
                    # Wait for network responses
                    await page.wait_for_timeout(5000)
                    break
            except Exception:
                pass

        if not clicked:
            print("   ⚠️ No shop anchor found via selectors. Trying JS-based discovery...", flush=True)
            # Try to find ANY clickable element related to shopping
            all_anchors = await page.evaluate("""() => {
                const results = [];
                // Check all <a> tags
                document.querySelectorAll('a').forEach(a => {
                    const href = a.href || '';
                    const text = a.textContent || '';
                    const cls = a.className || '';
                    if (href.includes('shop') || href.includes('product') || href.includes('pdp') ||
                        text.toLowerCase().includes('shop') || text.toLowerCase().includes('beli') ||
                        cls.includes('product') || cls.includes('commerce') || cls.includes('anchor') ||
                        cls.includes('shop') || cls.includes('cart')) {
                        results.push({
                            tag: a.tagName,
                            href: href.substring(0, 120),
                            text: text.substring(0, 60).trim(),
                            class: cls.substring(0, 80),
                            id: a.id || '',
                        });
                    }
                });
                // Check divs with click handlers / product classes
                document.querySelectorAll('[class*="product"], [class*="Product"], [class*="anchor"], [class*="Anchor"], [class*="commerce"], [class*="Commerce"]').forEach(el => {
                    results.push({
                        tag: el.tagName,
                        href: '',
                        text: (el.textContent || '').substring(0, 60).trim(),
                        class: (el.className || '').substring(0, 80),
                        id: el.id || '',
                    });
                });
                return results.slice(0, 20);
            }""")

            if all_anchors:
                print(f"   Found {len(all_anchors)} shopping-related elements via JS:", flush=True)
                for i, a in enumerate(all_anchors):
                    print(f"     [{i}] <{a['tag']}> class='{a['class']}' text='{a['text']}' href={a['href']}", flush=True)

                # Click the first one with a meaningful href or class
                for i, a in enumerate(all_anchors):
                    if a['href'] or 'product' in a['class'].lower() or 'anchor' in a['class'].lower():
                        try:
                            print(f"   🖱️ Clicking element [{i}]...", flush=True)
                            if a['class']:
                                await page.locator(f".{a['class'].split()[0]}").first.click(timeout=5000)
                            elif a['href']:
                                await page.locator(f'a[href*="{a["href"][:40]}"]').first.click(timeout=5000)
                            clicked = True
                            await page.wait_for_timeout(5000)
                            break
                        except Exception as e:
                            print(f"   ❌ Click failed: {e}", flush=True)
            else:
                print("   ❌ No shopping-related elements found in DOM", flush=True)

        p2_count = len(captured) - p1_count
        print(f"\n   → Captured {p2_count} new responses after click\n", flush=True)

        # ── Phase 3: Scroll and wait for lazy-loaded content ──
        print("📜 Phase 3: Scrolling to trigger lazy loads...", flush=True)
        for i in range(2):
            await page.evaluate(f"window.scrollBy(0, {400 * (i + 1)})")
            await page.wait_for_timeout(1500)

        p3_count = len(captured) - p1_count - p2_count
        print(f"   → Captured {p3_count} new responses after scroll\n", flush=True)

        # ── Phase 4: Check for popups/modals that appeared ────
        print("🔎 Phase 4: Checking for product popups/modals...", flush=True)
        popup_info = await page.evaluate("""() => {
            const modals = document.querySelectorAll('[class*="modal"], [class*="Modal"], [class*="popup"], [class*="Popup"], [class*="drawer"], [class*="Drawer"], [class*="bottom-sheet"], [class*="BottomSheet"], [role="dialog"]');
            const results = [];
            modals.forEach(m => {
                const visible = m.offsetParent !== null || m.style.display !== 'none';
                if (visible) {
                    results.push({
                        tag: m.tagName,
                        class: (m.className || '').substring(0, 100),
                        text: (m.textContent || '').substring(0, 200).trim(),
                        children: m.children.length,
                    });
                }
            });
            return results;
        }""")

        if popup_info:
            print(f"   Found {len(popup_info)} visible popup/modal:", flush=True)
            for pm in popup_info:
                print(f"     <{pm['tag']}> class='{pm['class']}' children={pm['children']}", flush=True)
                print(f"     text: {pm['text'][:150]}", flush=True)

            # If there's a product popup, try to click items inside it
            for pm in popup_info:
                if any(kw in pm['class'].lower() for kw in ['product', 'shop', 'commerce', 'anchor']):
                    print("   🖱️ Found product modal, clicking items inside...", flush=True)
                    try:
                        modal_links = page.locator(f".{pm['class'].split()[0]} a")
                        count = await modal_links.count()
                        if count > 0:
                            await modal_links.first.click(timeout=5000)
                            await page.wait_for_timeout(3000)
                    except Exception:
                        pass
        else:
            print("   No visible popups/modals found", flush=True)

        # ── Phase 5: Dump current page HTML for analysis ──────
        print("\n📄 Phase 5: Extracting page state...", flush=True)
        current_url = page.url
        print(f"   Current URL: {current_url}", flush=True)

        # Extract all script data that might have product info
        script_data = await page.evaluate("""() => {
            const results = {};
            // Check common global vars
            const globals = [
                '__UNIVERSAL_DATA_FOR_REHYDRATION__', 'SIGI_STATE',
                '__NEXT_DATA__', '__APP_STATE__',
            ];
            for (const g of globals) {
                try {
                    const val = window[g];
                    if (val) results[g] = typeof val === 'string' ? val.substring(0, 500) : JSON.stringify(val).substring(0, 500);
                } catch(e) {}
            }
            // Check for TikTok-specific data stores
            try {
                const scripts = document.querySelectorAll('script');
                let count = 0;
                scripts.forEach(s => {
                    const text = s.textContent || '';
                    if (text.includes('product') || text.includes('shop') || text.includes('anchor')) {
                        results[`script_${count}`] = text.substring(0, 300);
                        count++;
                    }
                });
            } catch(e) {}
            return results;
        }""")

        if script_data:
            print(f"   Found {len(script_data)} script data sources:", flush=True)
            for k, v in script_data.items():
                print(f"     {k}: {v[:120]}...", flush=True)

        await browser.close()

    # ── Analysis ──────────────────────────────────────────────
    total = len(captured)
    json_responses = [c for c in captured if c.is_json]
    product_responses = [c for c in captured if c.has_product_data()]

    print(f"\n{'='*80}", flush=True)
    print(f"📊 CAPTURE SUMMARY", flush=True)
    print(f"{'='*80}", flush=True)
    print(f"   Total interesting responses: {total}", flush=True)
    print(f"   JSON responses:              {len(json_responses)}", flush=True)
    print(f"   With product data:           {len(product_responses)}", flush=True)
    print(flush=True)

    # ── Dump all JSON responses ───────────────────────────────
    dump_dir = PROJECT_ROOT / "scripts" / "captured_responses"
    dump_dir.mkdir(exist_ok=True)

    # Clean old captures
    for old in dump_dir.glob("*.json"):
        old.unlink()

    print(f"💾 Saving responses to {dump_dir}/\n", flush=True)

    for i, cr in enumerate(json_responses):
        parsed_url = urlparse(cr.url)
        path_slug = re.sub(r'[^\w]', '_', parsed_url.path)[:60]
        fname = f"{i:03d}_{cr.status}_{path_slug}.json"
        fpath = dump_dir / fname

        meta = {
            "url": cr.url,
            "status": cr.status,
            "content_type": cr.content_type,
            "timing": cr.timing,
            "has_product_data": cr.has_product_data(),
            "body": cr.json_data,
        }
        fpath.write_text(json.dumps(meta, ensure_ascii=False, indent=2)[:500_000])

        product_flag = " 🛒" if cr.has_product_data() else ""
        print(f"  [{i:03d}]{product_flag} {fname}", flush=True)

    # ── Detailed analysis of product-containing responses ─────
    if product_responses:
        print(f"\n{'='*80}", flush=True)
        print(f"🛒 PRODUCT DATA FOUND IN {len(product_responses)} RESPONSE(S)", flush=True)
        print(f"{'='*80}", flush=True)

        for cr in product_responses:
            print(f"\n  URL: {cr.url[:150]}", flush=True)
            print(f"  Status: {cr.status} | Timing: {cr.timing}", flush=True)

            data = cr.json_data
            # Try to find and print product-specific data
            data_str = json.dumps(data, ensure_ascii=False)

            # Look for price fields
            prices = re.findall(r'"(?:price|sale_price|original_price|salePrice)":\s*"?(\d+)"?', data_str)
            if prices:
                print(f"  Prices found: {prices}", flush=True)

            # Look for product names
            names = re.findall(r'"(?:product_name|name|title)":\s*"([^"]{5,80})"', data_str)
            if names:
                print(f"  Product names: {names[:5]}", flush=True)

            # Look for sold/sales
            sales = re.findall(r'"(?:sold_count|sold|sales|order_count)":\s*"?([^",}]+)"?', data_str)
            if sales:
                print(f"  Sales data: {sales}", flush=True)

            # Print a compact summary of the response structure
            if isinstance(data, dict):
                print(f"  Top-level keys: {list(data.keys())[:15]}", flush=True)
                # Go one level deeper for interesting keys
                for k in data:
                    v = data[k]
                    if isinstance(v, dict):
                        sub_keys = list(v.keys())[:10]
                        if any(s in str(sub_keys).lower() for s in ['product', 'price', 'shop', 'item', 'goods']):
                            print(f"    [{k}] → {sub_keys}", flush=True)
                    elif isinstance(v, list) and len(v) > 0:
                        print(f"    [{k}] → list[{len(v)}], first item keys: {list(v[0].keys())[:10] if isinstance(v[0], dict) else type(v[0]).__name__}", flush=True)

            # Save full product response separately
            product_fname = f"PRODUCT_{product_responses.index(cr):02d}.json"
            product_fpath = dump_dir / product_fname
            product_fpath.write_text(json.dumps(data, ensure_ascii=False, indent=2)[:1_000_000])
            print(f"  💾 Full data saved to {product_fname}", flush=True)

    else:
        print("\n⚠️  No product data found in captured responses.", flush=True)
        print("    This could mean:", flush=True)
        print("    1. Product data is loaded via WebSocket (not XHR)", flush=True)
        print("    2. Product data is embedded in the initial HTML, not fetched separately", flush=True)
        print("    3. The video might not have active product links", flush=True)
        print("    4. Anti-bot measures prevented the data from loading", flush=True)

    # ── Print all captured URLs for manual inspection ─────────
    print(f"\n{'='*80}", flush=True)
    print(f"📋 ALL CAPTURED URLs ({total} total)", flush=True)
    print(f"{'='*80}", flush=True)
    for i, cr in enumerate(captured):
        json_flag = "JSON" if cr.is_json else "OTHER"
        product_flag = " 🛒" if cr.has_product_data() else ""
        body_size = len(cr.body)
        print(f"  [{i:03d}] {cr.status} [{json_flag}] ({body_size:,}B) {cr.timing} {cr.url[:150]}{product_flag}", flush=True)

    return captured, product_responses


# ── Entry point ───────────────────────────────────────────────

async def main():
    if len(sys.argv) < 2:
        # Default test URL
        video_url = "https://www.tiktok.com/@amosthiosa/video/7622536313668979976"
        print(f"No URL provided, using default: {video_url}", flush=True)
    else:
        video_url = sys.argv[1]

    # Run with headless=True for speed, headless=False to see the browser
    headless = "--visible" not in sys.argv

    captured, product_hits = await capture_network(video_url, headless=headless)

    print(f"\n{'='*80}", flush=True)
    print(f"✅ DONE — {len(captured)} responses captured, {len(product_hits)} with product data", flush=True)
    print(f"{'='*80}\n", flush=True)

    return captured, product_hits


if __name__ == "__main__":
    asyncio.run(main())
