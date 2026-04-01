#!/usr/bin/env python3
"""
Intercept ALL network requests on TikTok video page, 
especially after clicking the product anchor/cart icon.
Goal: find any API that returns price > 0.
"""
import asyncio, json, re, os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
PROXY = os.getenv("PROXY_URL", "")

VIDEO_URL = "https://www.tiktok.com/@amosthiosa/video/7622303845783309575"
OUT_DIR = Path(__file__).resolve().parent / "price_captures"
OUT_DIR.mkdir(exist_ok=True)


async def main():
    from playwright.async_api import async_playwright

    proxy_parts = PROXY.replace("http://", "").split("@")
    user_pass = proxy_parts[0].split(":")
    host_port = proxy_parts[1].split(":")
    pw_proxy = {
        "server": f"http://{host_port[0]}:{host_port[1]}",
        "username": user_pass[0],
        "password": user_pass[1],
    }

    price_hits = []
    all_apis = []
    response_idx = [0]

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        ctx = await browser.new_context(
            proxy=pw_proxy,
            user_agent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                "Version/17.0 Mobile/15E148 Safari/604.1"
            ),
            locale="id-ID",
            viewport={"width": 390, "height": 844},
            is_mobile=True,
            has_touch=True,
        )
        page = await ctx.new_page()

        async def on_response(response):
            url = response.url
            status = response.status
            ct = response.headers.get("content-type", "")

            # Log all API/XHR calls
            if "/api/" in url or "oec" in url or "shop" in url.lower():
                api_info = f"[{status}] {url[:150]}"
                all_apis.append(api_info)

            # Only check JSON/JS responses
            if "json" not in ct and "javascript" not in ct:
                return
            if status != 200:
                return

            try:
                body = await response.text()
            except:
                return

            # Search for non-zero price indicators
            has_price = bool(re.search(r'"price"\s*:\s*"?[1-9]', body))
            has_rp = "Rp" in body
            has_market_price = bool(re.search(r'"market_price"\s*:\s*"?[1-9]', body))
            has_sale_price = bool(re.search(r'"sale_price"\s*:\s*"?[1-9]', body))
            has_original = bool(re.search(r'"original_price"\s*:\s*"?[1-9]', body))
            has_min_price = bool(re.search(r'"min_price"\s*:\s*"?[1-9]', body))
            has_sku_price = bool(re.search(r'"sku_sell_price"\s*:\s*"?[1-9]', body))

            if any([has_price, has_rp, has_market_price, has_sale_price, has_original, has_min_price, has_sku_price]):
                idx = response_idx[0]
                response_idx[0] += 1

                fname = OUT_DIR / f"price_hit_{idx:03d}.json"
                with open(fname, "w") as f:
                    f.write(body)

                signals = []
                if has_price: signals.append("price>0")
                if has_rp: signals.append("Rp")
                if has_market_price: signals.append("market_price>0")
                if has_sale_price: signals.append("sale_price>0")
                if has_original: signals.append("original_price>0")
                if has_min_price: signals.append("min_price>0")
                if has_sku_price: signals.append("sku_sell_price>0")

                hit = {
                    "idx": idx,
                    "url": url[:200],
                    "signals": signals,
                    "size": len(body),
                    "file": str(fname),
                }
                price_hits.append(hit)
                print(f"\n💰 PRICE HIT #{idx}: {json.dumps(hit, indent=2)}", flush=True)

        page.on("response", on_response)

        # Step 1: Navigate to video
        print(f"📱 Opening video: {VIDEO_URL}", flush=True)
        try:
            await page.goto(VIDEO_URL, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"  Navigation error (continuing): {e}", flush=True)
        print("  Waiting 5s for page load...", flush=True)
        await page.wait_for_timeout(5000)

        # Step 2: Look for product/cart/anchor elements and click them
        print("\n🔍 Looking for product anchor elements...", flush=True)
        selectors_to_try = [
            # Common TikTok product selectors
            "[data-e2e='product-anchor']",
            "[class*='product-anchor']",
            "[class*='ProductAnchor']",
            "[class*='commerce']",
            "[class*='Commerce']",
            "[class*='shopping']",
            "[class*='cart']",
            "[class*='anchor-container']",
            "[class*='AnchorContainer']",
            # The basket/cart icon
            "a[href*='product']",
            "a[href*='shop']",
            # Div with product info
            "[class*='product-card']",
            "[class*='ProductCard']",
            # Generic clickable areas near product
            "[class*='ecommerce']",
            "[class*='pdp']",
            # Bottom bar anchors
            "[class*='bottom-bar'] a",
            "[class*='BottomBar'] a",
        ]

        clicked = False
        for sel in selectors_to_try:
            try:
                els = await page.query_selector_all(sel)
                if els:
                    print(f"  ✅ Found {len(els)} elements with selector: {sel}", flush=True)
                    for el in els[:2]:
                        try:
                            text = await el.text_content()
                            print(f"     Text: {(text or '').strip()[:100]}", flush=True)
                        except:
                            pass
                    # Click the first one
                    await els[0].click(force=True)
                    clicked = True
                    print(f"  👆 Clicked element with: {sel}", flush=True)
                    await page.wait_for_timeout(5000)
                    break
            except Exception as e:
                pass

        if not clicked:
            print("  ❌ No product anchor found via selectors", flush=True)
            # Try finding ANY clickable element related to products
            print("  Dumping all visible link texts...", flush=True)
            try:
                links = await page.evaluate("""() => {
                    const links = document.querySelectorAll('a, [role="button"], button');
                    return Array.from(links).slice(0, 50).map(a => ({
                        tag: a.tagName,
                        href: a.href || '',
                        text: (a.textContent || '').trim().substring(0, 80),
                        class: a.className || '',
                    }));
                }""")
                for link in links:
                    if any(kw in (link.get("text","") + link.get("class","") + link.get("href","")).lower() 
                           for kw in ["product", "shop", "cart", "anchor", "beli", "keranjang"]):
                        print(f"    🏷️  {link}", flush=True)
            except:
                pass

        # Step 3: Try scrolling/swiping to trigger lazy loading
        print("\n📜 Scrolling to trigger more loads...", flush=True)
        for i in range(3):
            await page.evaluate("window.scrollBy(0, 500)")
            await page.wait_for_timeout(2000)

        # Step 4: Extract data from page state
        print("\n📋 Extracting page state...", flush=True)
        try:
            page_data = await page.evaluate("""() => {
                // Look for SIGI_STATE, __NEXT_DATA__, or any global with product info
                const result = {};
                
                // Check window.__NEXT_DATA__
                if (window.__NEXT_DATA__) {
                    result.__NEXT_DATA__ = JSON.stringify(window.__NEXT_DATA__).substring(0, 500);
                }
                
                // Check SIGI_STATE
                if (window.SIGI_STATE) {
                    const ss = JSON.stringify(window.SIGI_STATE);
                    result.SIGI_STATE_size = ss.length;
                    // Look for price in SIGI_STATE
                    const priceMatch = ss.match(/"price"\s*:\s*"?(\d+)/g);
                    if (priceMatch) result.SIGI_price_matches = priceMatch.slice(0, 10);
                    // Look for anchors
                    const anchorMatch = ss.match(/"anchors"\s*:\s*\[/g);
                    if (anchorMatch) result.has_anchors = true;
                }
                
                // Check __UNIVERSAL_DATA_FOR_REHYDRATION__
                if (window.__UNIVERSAL_DATA_FOR_REHYDRATION__) {
                    const ud = JSON.stringify(window.__UNIVERSAL_DATA_FOR_REHYDRATION__);
                    result.UNIVERSAL_DATA_size = ud.length;
                    const priceMatch = ud.match(/"price"\s*:\s*"?(\d+)/g);
                    if (priceMatch) result.UNIVERSAL_price_matches = priceMatch.slice(0, 10);
                }

                // Check for any global variable containing product data
                const globals = Object.keys(window).filter(k => 
                    typeof window[k] === 'object' && window[k] !== null
                );
                result.global_objects_count = globals.length;
                
                return result;
            }""")
            print(f"  Page state: {json.dumps(page_data, indent=2)}", flush=True)
        except Exception as e:
            print(f"  Error extracting page state: {e}", flush=True)

        # Step 5: Deep dive into SIGI_STATE anchors
        print("\n🔎 Deep dive into anchor data...", flush=True)
        try:
            anchor_data = await page.evaluate("""() => {
                if (!window.SIGI_STATE) return {error: "No SIGI_STATE"};
                const ss = JSON.stringify(window.SIGI_STATE);
                const state = window.SIGI_STATE;
                
                // Navigate to item info
                let items = [];
                try {
                    const itemModule = state.ItemModule || {};
                    for (const [key, item] of Object.entries(itemModule)) {
                        if (item.anchors && item.anchors.length > 0) {
                            items.push({
                                id: key,
                                anchor_count: item.anchors.length,
                                first_anchor: JSON.stringify(item.anchors[0]).substring(0, 1000),
                            });
                        }
                    }
                } catch(e) {}
                
                // Also check for commerce module
                let commerce = null;
                try {
                    if (state.CommerceModule) {
                        commerce = JSON.stringify(state.CommerceModule).substring(0, 2000);
                    }
                } catch(e) {}
                
                // Also try videoDetail
                let videoDetail = null;
                try {
                    const ud = window.__UNIVERSAL_DATA_FOR_REHYDRATION__;
                    if (ud) {
                        const defaultScope = ud['__DEFAULT_SCOPE__'] || {};
                        const vid = defaultScope['webapp.video-detail'];
                        if (vid && vid.itemInfo && vid.itemInfo.itemStruct) {
                            const struct = vid.itemInfo.itemStruct;
                            if (struct.anchors) {
                                videoDetail = {
                                    anchor_count: struct.anchors.length,
                                    anchors: struct.anchors.map(a => ({
                                        type: a.type,
                                        keyword: a.keyword,
                                        extra: (a.extra || '').substring(0, 500),
                                        id: a.id,
                                    }))
                                };
                            }
                        }
                    }
                } catch(e) {}
                
                return {items, commerce, videoDetail};
            }""")
            print(f"  Anchor data: {json.dumps(anchor_data, indent=2, ensure_ascii=False)[:5000]}", flush=True)
            
            # If we have anchor extra data, parse it for prices
            if anchor_data.get("videoDetail") and anchor_data["videoDetail"].get("anchors"):
                for anc in anchor_data["videoDetail"]["anchors"]:
                    extra_str = anc.get("extra", "")
                    if extra_str:
                        try:
                            extra = json.loads(extra_str)
                            if isinstance(extra, list) and len(extra) > 0:
                                inner = extra[0]
                                if isinstance(inner, dict) and "extra" in inner:
                                    product = json.loads(inner["extra"])
                                    price = product.get("price", 0)
                                    market_price = product.get("market_price", 0)
                                    title = product.get("title", "")
                                    print(f"\n  📦 Product: {title[:60]}", flush=True)
                                    print(f"     price={price}, market_price={market_price}", flush=True)
                                    # Check ALL fields for price info
                                    for k, v in product.items():
                                        if "price" in k.lower() or "cost" in k.lower() or "amount" in k.lower():
                                            print(f"     {k}={v}", flush=True)
                                    # Check SKUs
                                    skus = product.get("skus", [])
                                    if skus:
                                        for sku in skus[:3]:
                                            print(f"     SKU: sell_price={sku.get('sku_sell_price',0)}, market_price={sku.get('sku_market_price',0)}, origin_price={sku.get('origin_price',0)}", flush=True)
                        except:
                            pass
        except Exception as e:
            print(f"  Error: {e}", flush=True)

        # Summary
        print(f"\n{'='*60}", flush=True)
        print(f"📊 Summary:", flush=True)
        print(f"  Total API calls logged: {len(all_apis)}", flush=True)
        print(f"  Price hits: {len(price_hits)}", flush=True)
        for hit in price_hits:
            print(f"    #{hit['idx']}: {hit['signals']} - {hit['url'][:100]}", flush=True)
        print(f"\n  All API calls:", flush=True)
        for api in all_apis:
            print(f"    {api}", flush=True)

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
