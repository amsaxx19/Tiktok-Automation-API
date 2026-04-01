#!/usr/bin/env python3
"""
Capture product DETAIL network calls.
Opens a TikTok video → clicks keranjang kuning → captures all API calls
that contain price/sold/product detail data.

Also tries direct OEC API hits.
"""
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse, unquote

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


def proxy_config():
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


async def capture_product_detail(video_url: str):
    from playwright.async_api import async_playwright

    proxy = proxy_config()
    captured = []
    t0 = time.time()

    print(f"{'='*80}", flush=True)
    print(f"🔍 Product Detail Capture — {video_url}", flush=True)
    print(f"{'='*80}\n", flush=True)

    async def on_response(response):
        url = response.url
        # Capture EVERYTHING with JSON content
        try:
            ct = response.headers.get("content-type", "")
            if "json" not in ct and "javascript" not in ct:
                return
            body = await response.body()
            if len(body) < 20:
                return
            elapsed = f"{time.time() - t0:.1f}s"
            try:
                data = json.loads(body)
            except Exception:
                return

            text = json.dumps(data).lower()
            # Check if this response has price/product detail signals
            detail_signals = [
                "price", "sold", "shop_name", "seller_name",
                "product_name", "rating", "review", "stock",
                "discount", "commission", "flash_sale",
                "original_price", "sale_price", "market_price",
            ]
            matched = [s for s in detail_signals if s in text]

            if matched:
                captured.append({
                    "url": url,
                    "status": response.status,
                    "timing": elapsed,
                    "signals": matched,
                    "data": data,
                    "size": len(body),
                })
                icon = "🛒" if len(matched) >= 3 else "📦"
                print(f"  [{elapsed}] {icon} {response.status} ({len(body):,}B) signals={matched}", flush=True)
                print(f"       URL: {url[:150]}", flush=True)
        except Exception:
            pass

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, proxy=proxy)
        context = await browser.new_context(
            locale='id-ID',
            viewport={'width': 412, 'height': 915},
            user_agent=MOBILE_UA,
            extra_http_headers={'Accept-Language': 'id-ID,id;q=0.9,en;q=0.5'},
        )
        page = await context.new_page()
        page.on("response", on_response)

        # Phase 1: Load video page
        print("📡 Phase 1: Loading video...", flush=True)
        try:
            await page.goto(video_url, wait_until='domcontentloaded', timeout=60000)
        except Exception as e:
            print(f"  ⚠️ Nav: {e}", flush=True)
        await page.wait_for_timeout(5000)
        print(f"  → Phase 1 done, {len(captured)} detail responses\n", flush=True)

        # Phase 2: Find and click the product anchor
        print("🛒 Phase 2: Clicking product anchor...", flush=True)
        product_anchor_clicked = False

        # Try clicking the ecom anchor container (the keranjang kuning bar)
        ecom_selectors = [
            '[class*="DivEcomAnchorMobile"]',
            '[class*="ecom-anchor"]',
            '[class*="product-anchor"]',
            '[class*="EcomAnchor"]',
            '[class*="anchor-container"]',
            'a[href*="shop-id."]',
            'a[href*="tokopedia.com"]',
        ]
        for sel in ecom_selectors:
            try:
                loc = page.locator(sel)
                cnt = await loc.count()
                if cnt > 0:
                    # Use force=True to bypass overlay interceptions
                    await loc.first.click(force=True, timeout=3000)
                    product_anchor_clicked = True
                    print(f"  ✅ Clicked: {sel} ({cnt}x)", flush=True)
                    await page.wait_for_timeout(6000)
                    break
            except Exception as e:
                print(f"  ❌ {sel}: {e.__class__.__name__}", flush=True)

        if not product_anchor_clicked:
            # JavaScript-based click
            print("  ⚠️ Trying JS dispatch click...", flush=True)
            await page.evaluate("""() => {
                const anchors = document.querySelectorAll('a[href*="shop-id"], a[href*="tokopedia"], [class*="EcomAnchor"], [class*="ecom-anchor"]');
                for (const a of anchors) {
                    a.click();
                    a.dispatchEvent(new MouseEvent('click', {bubbles: true}));
                    break;
                }
            }""")
            await page.wait_for_timeout(6000)

        print(f"  → Phase 2 done, {len(captured)} detail responses\n", flush=True)

        # Phase 3: Check if we navigated to a product page or popup opened
        print("🔎 Phase 3: Checking current state...", flush=True)
        current_url = page.url
        print(f"  Current URL: {current_url}", flush=True)

        # Check for any modals/drawers that opened
        modal_text = await page.evaluate("""() => {
            const modals = document.querySelectorAll('[role="dialog"], [class*="modal"], [class*="Modal"], [class*="drawer"], [class*="Drawer"], [class*="BottomSheet"], [class*="bottom-sheet"]');
            const results = [];
            for (const m of modals) {
                if (m.offsetParent !== null) {
                    results.push({
                        class: (m.className || '').substring(0, 100),
                        text: (m.textContent || '').substring(0, 300).trim(),
                        html: m.innerHTML.substring(0, 500),
                    });
                }
            }
            return results;
        }""")
        if modal_text:
            print(f"  Found {len(modal_text)} visible modal(s):", flush=True)
            for mt in modal_text:
                print(f"    class: {mt['class'][:80]}", flush=True)
                print(f"    text: {mt['text'][:200]}", flush=True)
        else:
            print("  No modals found", flush=True)

        # Phase 4: Try navigating to the product's OEC detail URL
        print("\n🌐 Phase 4: Trying OEC product detail URL...", flush=True)

        # Extract product_id from page script
        product_info = await page.evaluate("""() => {
            try {
                const scripts = document.querySelectorAll('script');
                for (const s of scripts) {
                    const t = s.textContent || '';
                    if (t.includes('"videoDetail"')) {
                        const data = JSON.parse(t);
                        const item = data?.videoDetail?.itemInfo?.itemStruct || {};
                        const anchors = item.anchors || [];
                        if (anchors.length > 0) {
                            const extra = JSON.parse(anchors[0].extra || '[]');
                            if (extra.length > 0) {
                                const inner = JSON.parse(extra[0].extra || '{}');
                                return {
                                    product_id: String(inner.product_id || ''),
                                    detail_url: inner.detail_url || '',
                                    seo_url: inner.seo_url || '',
                                    seller_id: String(inner.seller_id || ''),
                                    schema: inner.schema || '',
                                };
                            }
                        }
                    }
                }
            } catch(e) { return {error: e.message}; }
            return null;
        }""")

        print(f"  Product info: {json.dumps(product_info, indent=2)[:500]}", flush=True)

        if product_info and product_info.get('product_id'):
            pid = product_info['product_id']

            # Try 1: OEC API direct product detail
            oec_urls = [
                f"https://oec-api.tiktokv.com/api/v1/product/detail?product_id={pid}&region=ID&language=id",
                f"https://oec-api-sg.tiktokv.com/api/v1/product/detail?product_id={pid}&region=ID&language=id",
                f"https://www.tiktok.com/api/product/detail/?product_id={pid}&region=ID",
                f"https://shop-id.tokopedia.com/api/product/{pid}",
            ]

            for oec_url in oec_urls:
                print(f"\n  🔗 Trying: {oec_url[:120]}", flush=True)
                try:
                    new_page = await context.new_page()
                    new_page.on("response", on_response)
                    try:
                        resp = await new_page.goto(oec_url, wait_until='domcontentloaded', timeout=15000)
                        if resp:
                            ct = resp.headers.get("content-type", "")
                            print(f"     Status: {resp.status}, CT: {ct[:50]}", flush=True)
                            if "json" in ct:
                                body = await resp.body()
                                try:
                                    data = json.loads(body)
                                    print(f"     Keys: {list(data.keys())[:10]}", flush=True)
                                    text = json.dumps(data).lower()
                                    if "price" in text or "sold" in text:
                                        print(f"     🛒 HAS PRICE/SOLD DATA!", flush=True)
                                        captured.append({
                                            "url": oec_url,
                                            "status": resp.status,
                                            "timing": f"{time.time()-t0:.1f}s",
                                            "signals": ["direct_api"],
                                            "data": data,
                                            "size": len(body),
                                        })
                                except Exception:
                                    print(f"     Body (first 200): {(await resp.text())[:200]}", flush=True)
                    except Exception as e:
                        print(f"     ❌ {e.__class__.__name__}: {str(e)[:100]}", flush=True)
                    await new_page.close()
                except Exception as e:
                    print(f"     ❌ Page error: {e}", flush=True)

            # Try 2: Tokopedia SEO URL (product page)
            seo_url = product_info.get('seo_url', '')
            if seo_url:
                print(f"\n  🔗 Trying SEO URL: {seo_url[:120]}", flush=True)
                try:
                    new_page = await context.new_page()
                    new_page.on("response", on_response)
                    try:
                        await new_page.goto(seo_url, wait_until='domcontentloaded', timeout=20000)
                    except Exception:
                        pass
                    await new_page.wait_for_timeout(5000)

                    # Check page content
                    page_text = await new_page.evaluate("() => document.body?.innerText?.substring(0, 1000) || ''")
                    print(f"     Page text: {page_text[:300]}", flush=True)

                    # Look for price in rendered DOM
                    price_data = await new_page.evaluate("""() => {
                        const results = {};
                        // Look for price elements
                        document.querySelectorAll('[class*="price"], [class*="Price"], [data-testid*="price"]').forEach(el => {
                            results['price_el_' + (el.className||'').substring(0,40)] = el.textContent.trim().substring(0, 50);
                        });
                        // Look for sold/rating
                        document.querySelectorAll('[class*="sold"], [class*="Sold"], [class*="rating"], [class*="Rating"]').forEach(el => {
                            results['sold_el_' + (el.className||'').substring(0,40)] = el.textContent.trim().substring(0, 50);
                        });
                        // Look for shop name
                        document.querySelectorAll('[class*="shop"], [class*="Shop"], [class*="seller"], [class*="Seller"]').forEach(el => {
                            results['shop_el_' + (el.className||'').substring(0,40)] = el.textContent.trim().substring(0, 100);
                        });
                        // Look for structured data
                        const ld = document.querySelector('script[type="application/ld+json"]');
                        if (ld) results['ld_json'] = ld.textContent.substring(0, 500);
                        return results;
                    }""")
                    if price_data:
                        print(f"     DOM data:", flush=True)
                        for k, v in price_data.items():
                            print(f"       {k}: {v}", flush=True)
                    await new_page.close()
                except Exception as e:
                    print(f"     ❌ {e}", flush=True)

        await browser.close()

    # Summary
    print(f"\n{'='*80}", flush=True)
    print(f"📊 SUMMARY: {len(captured)} responses with detail signals", flush=True)
    print(f"{'='*80}", flush=True)

    dump_dir = PROJECT_ROOT / "scripts" / "captured_responses"
    dump_dir.mkdir(exist_ok=True)

    for i, c in enumerate(captured):
        fname = f"detail_{i:03d}.json"
        (dump_dir / fname).write_text(json.dumps(c, ensure_ascii=False, indent=2)[:500_000])
        print(f"  [{i}] {c['status']} signals={c['signals']} size={c['size']:,}B", flush=True)
        print(f"      URL: {c['url'][:150]}", flush=True)

        # Show price/sold data if found
        data = c['data']
        text = json.dumps(data, ensure_ascii=False)
        prices = re.findall(r'"(?:price|sale_price|original_price|market_price|salePrice|originPrice)":\s*"?([^",}\]]{1,30})"?', text)
        sold = re.findall(r'"(?:sold|sold_count|soldCount|sales|order_count)":\s*"?([^",}\]]{1,30})"?', text)
        shops = re.findall(r'"(?:shop_name|shopName|seller_name|sellerName)":\s*"([^"]{1,80})"', text)
        if prices:
            print(f"      💰 Prices: {prices[:5]}", flush=True)
        if sold:
            print(f"      📈 Sold: {sold[:5]}", flush=True)
        if shops:
            print(f"      🏪 Shops: {shops[:5]}", flush=True)


async def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "https://www.tiktok.com/@amosthiosa/video/7622303845783309575"
    await capture_product_detail(url)


if __name__ == "__main__":
    asyncio.run(main())
