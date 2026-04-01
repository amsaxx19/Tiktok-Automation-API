#!/usr/bin/env python3
"""
Final comprehensive attempt to get product prices.
Strategy: Use the TikTok video page's own JavaScript rendering
to click the product anchor and capture the product detail panel data.

Key insight: When a user clicks the cart/product icon on a TikTok video,
TikTok opens a product panel/drawer that loads product details INCLUDING price.
We need to intercept that.
"""
import asyncio, json, re, os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
PROXY = os.getenv("PROXY_URL", "")
VIDEO_URL = "https://www.tiktok.com/@amosthiosa/video/7622303845783309575"
OUT_DIR = Path(__file__).resolve().parent / "price_final"
OUT_DIR.mkdir(exist_ok=True)


async def main():
    from patchright.async_api import async_playwright

    proxy_parts = PROXY.replace("http://", "").split("@")
    user_pass = proxy_parts[0].split(":")
    host_port = proxy_parts[1].split(":")
    pw_proxy = {
        "server": f"http://{host_port[0]}:{host_port[1]}",
        "username": user_pass[0],
        "password": user_pass[1],
    }

    all_responses = []
    idx_counter = [0]

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            proxy=pw_proxy,
            locale="id-ID",
            viewport={"width": 1366, "height": 768},
        )
        page = await ctx.new_page()

        async def on_response(response):
            url = response.url
            ct = response.headers.get("content-type", "")
            status = response.status

            # Skip static resources
            if any(ext in url for ext in [".js", ".css", ".png", ".jpg", ".svg", ".woff", ".gif", ".ico", ".mp4", ".webp"]):
                return

            if status == 200 and ("json" in ct or "text/plain" in ct):
                try:
                    body = await response.text()
                    if len(body) > 50:
                        i = idx_counter[0]
                        idx_counter[0] += 1
                        
                        # Save
                        fname = OUT_DIR / f"resp_{i:03d}.json"
                        with open(fname, "w") as f:
                            f.write(body)
                        
                        # Check for price signals
                        lower = body.lower()
                        signals = []
                        if re.search(r'"price"\s*:\s*"?[1-9]', body): signals.append("price>0")
                        if re.search(r'"market_price"\s*:\s*"?[1-9]', body): signals.append("market_price>0")
                        if re.search(r'"sell_price"\s*:\s*"?[1-9]', body): signals.append("sell_price>0")
                        if re.search(r'"display_price"\s*:\s*"?[1-9Rr]', body): signals.append("display_price")
                        if "product_id" in lower and "price" in lower: signals.append("product+price")
                        
                        label = f"[{i:03d}] {status} {url[:100]} ({len(body)}B)"
                        if signals:
                            label += f" 💰{signals}"
                        
                        all_responses.append({"i": i, "url": url, "size": len(body), "signals": signals, "file": str(fname)})
                        
                        if signals:
                            print(f"  💰 {label}", flush=True)
                except:
                    pass

        page.on("response", on_response)

        # Step 1: Load the video page
        print(f"📱 Loading: {VIDEO_URL}", flush=True)
        try:
            await page.goto(VIDEO_URL, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"  Error: {e}", flush=True)
        await page.wait_for_timeout(6000)
        print(f"  Loaded. URL: {page.url}", flush=True)
        print(f"  Title: {await page.title()}", flush=True)

        # Step 2: Find the product anchor/cart icon
        # On TikTok desktop, there's usually a shopping cart icon or product card
        print("\n🔍 Looking for product panel trigger...", flush=True)
        
        # Dump all interactive elements
        elements = await page.evaluate("""() => {
            const all = document.querySelectorAll('a, button, [role="button"], [class*="anchor"], [class*="product"], [class*="cart"], [class*="shop"], [data-e2e]');
            return Array.from(all).slice(0, 100).map(el => ({
                tag: el.tagName,
                text: (el.textContent || '').trim().substring(0, 80),
                href: el.href || el.getAttribute('href') || '',
                class: (el.className || '').substring(0, 100),
                dataE2e: el.getAttribute('data-e2e') || '',
                rect: el.getBoundingClientRect().toJSON(),
            })).filter(el => 
                el.text.length > 0 || 
                el.href.length > 0 || 
                el.class.includes('anchor') || 
                el.class.includes('product') || 
                el.class.includes('cart') ||
                el.class.includes('shop') ||
                el.dataE2e.length > 0
            );
        }""")
        
        print(f"  Found {len(elements)} interactive elements", flush=True)
        
        # Filter for product-related
        product_els = []
        for el in elements:
            combined = f"{el['text']} {el['class']} {el['href']} {el['dataE2e']}".lower()
            if any(kw in combined for kw in ['product', 'shop', 'cart', 'anchor', 'beli', 'keranjang', 'commerce']):
                product_els.append(el)
                print(f"  🏷️  tag={el['tag']} class={el['class'][:60]} text={el['text'][:40]} href={el['href'][:60]} e2e={el['dataE2e']}", flush=True)
        
        # Step 3: Try to click product-related elements
        if product_els:
            for pel in product_els[:3]:
                print(f"\n  👆 Clicking: {pel['text'][:40] or pel['class'][:40]}...", flush=True)
                
                # Find and click the element
                try:
                    if pel['dataE2e']:
                        selector = f"[data-e2e='{pel['dataE2e']}']"
                    elif pel['href'] and 'shop' in pel['href']:
                        selector = f"a[href*='shop']"
                    elif 'anchor' in pel['class'].lower():
                        selector = f".{pel['class'].split()[0]}"
                    else:
                        continue
                    
                    el = await page.query_selector(selector)
                    if el:
                        await el.click(force=True)
                        print(f"  Clicked! Waiting for responses...", flush=True)
                        await page.wait_for_timeout(5000)
                        
                        # Check if a modal/panel opened
                        modal = await page.query_selector("[class*='modal'], [class*='drawer'], [class*='panel'], [class*='popup'], [class*='overlay']")
                        if modal:
                            modal_text = (await modal.text_content() or "").strip()
                            print(f"  📋 Modal/panel found! Text: {modal_text[:200]}", flush=True)
                            rp = re.findall(r"Rp\s?[\d.,]+", modal_text)
                            if rp:
                                print(f"  💰 Prices in modal: {rp}", flush=True)
                except Exception as e:
                    print(f"  Click error: {e}", flush=True)
        
        # Step 4: Check __UNIVERSAL_DATA_FOR_REHYDRATION__ for anchor extra data
        print("\n📋 Checking page data for anchor details...", flush=True)
        try:
            anchor_info = await page.evaluate(r"""() => {
                const ud = window.__UNIVERSAL_DATA_FOR_REHYDRATION__;
                if (!ud) return {error: "No UNIVERSAL_DATA"};
                
                const ds = ud['__DEFAULT_SCOPE__'] || {};
                const vid = ds['webapp.video-detail'];
                if (!vid) return {error: "No video-detail"};
                
                const struct = vid.itemInfo?.itemStruct;
                if (!struct) return {error: "No itemStruct"};
                
                const anchors = struct.anchors || [];
                if (anchors.length === 0) return {error: "No anchors"};
                
                const results = [];
                for (const anc of anchors) {
                    const ancData = {
                        type: anc.type,
                        keyword: anc.keyword,
                        id: anc.id,
                        // Get ALL properties
                        allKeys: Object.keys(anc),
                    };
                    
                    // Parse extra
                    try {
                        const extra = JSON.parse(anc.extra);
                        if (Array.isArray(extra)) {
                            for (const entry of extra) {
                                if (entry.extra) {
                                    const prod = JSON.parse(entry.extra);
                                    ancData.product = {
                                        title: prod.title,
                                        product_id: prod.product_id,
                                        price: prod.price,
                                        market_price: prod.market_price,
                                        currency: prod.currency,
                                        sold_count: prod.sold_count,
                                        shop_name: prod.shop_name,
                                        rating: prod.rating,
                                        review_count: prod.review_count,
                                        // Dump ALL keys
                                        allKeys: Object.keys(prod),
                                        // Dump ALL non-object values
                                        allValues: Object.fromEntries(
                                            Object.entries(prod)
                                                .filter(([k,v]) => typeof v !== 'object' || v === null)
                                                .map(([k,v]) => [k, String(v).substring(0, 100)])
                                        ),
                                    };
                                }
                            }
                        }
                    } catch(e) {}
                    
                    // Also check icon, logExtra, etc
                    if (anc.logExtra) {
                        try {
                            ancData.logExtra = JSON.parse(anc.logExtra);
                        } catch(e) {
                            ancData.logExtra = anc.logExtra;
                        }
                    }
                    
                    results.push(ancData);
                }
                
                return {anchorCount: anchors.length, results};
            }""")
            
            print(json.dumps(anchor_info, indent=2, ensure_ascii=False)[:8000], flush=True)
        except Exception as e:
            print(f"  Error: {e}", flush=True)
        
        # Summary
        print(f"\n{'='*60}", flush=True)
        print(f"📊 Total responses captured: {idx_counter[0]}", flush=True)
        price_hits = [r for r in all_responses if r["signals"]]
        print(f"   Price hits: {len(price_hits)}", flush=True)
        for h in price_hits:
            print(f"   #{h['i']}: {h['signals']} - {h['url'][:80]}", flush=True)

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
