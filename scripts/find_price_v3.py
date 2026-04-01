#!/usr/bin/env python3
"""
Focused script: Click product anchor on TikTok video, intercept price APIs.
"""
import asyncio, json, re, os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
PROXY = os.getenv("PROXY_URL", "")
VIDEO_URL = "https://www.tiktok.com/@amosthiosa/video/7622303845783309575"
OUT_DIR = Path(__file__).resolve().parent / "price_captures2"
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
    all_requests = []
    idx = [0]

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
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

            # Skip static assets
            if any(ext in url for ext in [".js", ".css", ".png", ".jpg", ".svg", ".woff", ".ico", ".gif"]):
                return

            all_requests.append(f"[{status}] {ct[:30]:30s} {url[:150]}")

            if status != 200:
                return
            if "json" not in ct:
                return

            try:
                body = await response.text()
            except:
                return

            # Save all JSON responses for analysis
            i = idx[0]
            idx[0] += 1
            fname = OUT_DIR / f"resp_{i:03d}.json"
            with open(fname, "w") as f:
                f.write(body)

            # Check for price signals
            has_nonzero_price = bool(re.search(r'"price"\s*:\s*"?[1-9]', body))
            has_rp = bool(re.search(r'Rp\s*[\d.]+', body))
            has_market = bool(re.search(r'"market_price"\s*:\s*"?[1-9]', body))
            has_sell = bool(re.search(r'"sell_price"\s*:\s*"?[1-9]', body))
            has_sku_sell = bool(re.search(r'"sku_sell_price"\s*:\s*"?[1-9]', body))
            has_min = bool(re.search(r'"min_price"\s*:\s*"?[1-9]', body))
            has_display = bool(re.search(r'"display_price"\s*:\s*"?[1-9Rr]', body))

            signals = []
            if has_nonzero_price: signals.append("price>0")
            if has_rp: signals.append("Rp")
            if has_market: signals.append("market_price>0")
            if has_sell: signals.append("sell_price>0")
            if has_sku_sell: signals.append("sku_sell_price>0")
            if has_min: signals.append("min_price>0")
            if has_display: signals.append("display_price")

            print(f"  📥 JSON #{i}: {url[:100]}  size={len(body)}  signals={signals or 'none'}", flush=True)

            if signals:
                price_hits.append({
                    "idx": i,
                    "url": url[:200],
                    "signals": signals,
                    "size": len(body),
                    "file": str(fname),
                })

        page.on("response", on_response)

        # Navigate
        print(f"📱 Opening: {VIDEO_URL}", flush=True)
        try:
            await page.goto(VIDEO_URL, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"  Nav error (continuing): {e}", flush=True)
        await page.wait_for_timeout(6000)
        print("  Page loaded.", flush=True)

        # Find and click product anchor
        print("\n🔍 Looking for product links...", flush=True)
        shop_links = await page.query_selector_all("a[href*='shop']")
        product_links = await page.query_selector_all("a[href*='product']")
        anchor_links = await page.query_selector_all("[class*='anchor']")
        
        all_found = []
        for el in shop_links:
            href = await el.get_attribute("href") or ""
            text = (await el.text_content() or "").strip()[:80]
            all_found.append(("shop-link", href[:120], text))
        for el in product_links:
            href = await el.get_attribute("href") or ""
            text = (await el.text_content() or "").strip()[:80]
            all_found.append(("product-link", href[:120], text))
        for el in anchor_links:
            text = (await el.text_content() or "").strip()[:80]
            cls = await el.get_attribute("class") or ""
            all_found.append(("anchor-class", cls[:80], text))

        for item in all_found:
            print(f"  Found: {item}", flush=True)

        # Click the first shop link (should open product panel or navigate)
        if shop_links:
            print("\n👆 Clicking shop link...", flush=True)
            href_before = await shop_links[0].get_attribute("href")
            print(f"  Link href: {href_before}", flush=True)
            
            # See if clicking opens a popup/panel or navigates
            try:
                # Listen for new pages (popup)
                async with ctx.expect_page(timeout=10000) as new_page_info:
                    await shop_links[0].click(force=True)
                new_page = await new_page_info.value
                await new_page.wait_for_load_state("domcontentloaded", timeout=15000)
                await new_page.wait_for_timeout(3000)
                print(f"  🆕 New page opened: {new_page.url}", flush=True)
                
                # Check new page for prices
                content = await new_page.content()
                rp_prices = re.findall(r"Rp\s?[\d.,]+", content)
                if rp_prices:
                    print(f"  💰 Prices in new page: {rp_prices[:10]}", flush=True)
                
                # Also try to get structured data from new page
                try:
                    ld_json = await new_page.evaluate("""() => {
                        const scripts = document.querySelectorAll('script[type="application/ld+json"]');
                        return Array.from(scripts).map(s => s.textContent);
                    }""")
                    for ld in ld_json:
                        if "price" in ld.lower():
                            print(f"  💰 LD+JSON: {ld[:500]}", flush=True)
                except:
                    pass
                
                # Check for meta tags with price
                try:
                    meta_price = await new_page.evaluate("""() => {
                        const metas = document.querySelectorAll('meta[property*="price"], meta[name*="price"]');
                        return Array.from(metas).map(m => ({
                            property: m.getAttribute('property') || m.getAttribute('name'),
                            content: m.getAttribute('content'),
                        }));
                    }""")
                    if meta_price:
                        print(f"  💰 Meta prices: {meta_price}", flush=True)
                except:
                    pass
                    
                # Check page text for price
                try:
                    price_text = await new_page.evaluate("""() => {
                        const body = document.body.innerText;
                        const lines = body.split('\\n');
                        return lines.filter(l => l.match(/Rp|price|harga/i)).slice(0, 20);
                    }""")
                    if price_text:
                        print(f"  💰 Price text lines: {price_text[:10]}", flush=True)
                except:
                    pass

                await new_page.close()
            except Exception as e:
                print(f"  No new page (popup): {e}", flush=True)
                # Check if current page changed
                await page.wait_for_timeout(5000)
                print(f"  Current URL: {page.url}", flush=True)

        # Also try extracting from page script directly
        print("\n📋 Checking SIGI_STATE / UNIVERSAL_DATA for prices...", flush=True)
        try:
            result = await page.evaluate(r"""() => {
                const out = {};
                
                // Check UNIVERSAL_DATA anchors
                const ud = window.__UNIVERSAL_DATA_FOR_REHYDRATION__;
                if (ud) {
                    const ds = ud['__DEFAULT_SCOPE__'] || {};
                    const vid = ds['webapp.video-detail'];
                    if (vid && vid.itemInfo && vid.itemInfo.itemStruct && vid.itemInfo.itemStruct.anchors) {
                        out.anchors = vid.itemInfo.itemStruct.anchors.map(a => {
                            let parsed = null;
                            try {
                                const extra = JSON.parse(a.extra);
                                if (Array.isArray(extra) && extra.length > 0) {
                                    const inner = extra[0];
                                    if (inner.extra) {
                                        const prod = JSON.parse(inner.extra);
                                        parsed = {
                                            title: prod.title,
                                            price: prod.price,
                                            market_price: prod.market_price,
                                            currency: prod.currency,
                                            sold_count: prod.sold_count,
                                            shop_name: prod.shop_name,
                                            skus: (prod.skus || []).map(s => ({
                                                sku_sell_price: s.sku_sell_price,
                                                sku_market_price: s.sku_market_price,
                                                origin_price: s.origin_price,
                                                title: s.title,
                                            })),
                                            // Dump ALL keys to find price-like fields
                                            all_keys: Object.keys(prod),
                                        };
                                    }
                                }
                            } catch(e) {}
                            return {
                                type: a.type,
                                keyword: a.keyword,
                                id: a.id,
                                parsed: parsed,
                            };
                        });
                    }
                }
                
                return out;
            }""")
            print(json.dumps(result, indent=2, ensure_ascii=False)[:5000], flush=True)
        except Exception as e:
            print(f"  Error: {e}", flush=True)

        # Summary
        print(f"\n{'='*60}", flush=True)
        print(f"📊 JSON responses captured: {idx[0]}", flush=True)
        print(f"   Price hits: {len(price_hits)}", flush=True)
        for hit in price_hits:
            print(f"   #{hit['idx']}: signals={hit['signals']} url={hit['url'][:80]}", flush=True)
        print(f"\n   All non-static requests:", flush=True)
        for r in all_requests:
            print(f"   {r}", flush=True)

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
