"""
Price discovery v15 — FOCUSED: TikTok network capture only.
Captures EVERY single network request when clicking product anchor.
Also checks: what happens when navigating to the detail_url from the product JSON.
"""
import asyncio
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PROXY_URL = os.getenv("PROXY_URL", "http://5sjQhR7dWXPoSuv:gAbLujfGLSP2rWU@178.93.21.156:49644")
PRODUCT_ID = "1732773678384055322"
VIDEO_URL = "https://www.tiktok.com/@amosthiosa/video/7485069092459463953"

OUTDIR = os.path.join(os.path.dirname(__file__), "price_final9")
os.makedirs(OUTDIR, exist_ok=True)


async def full_capture():
    """Capture every network response from TikTok video page."""
    from playwright.async_api import async_playwright
    
    proxy_match = re.match(r'^(https?://)([^:]+):([^@]+)@([^:]+):(\d+)$', PROXY_URL)
    proxy_config = {
        'server': f"{proxy_match.group(1)}{proxy_match.group(4)}:{proxy_match.group(5)}",
        'username': proxy_match.group(2),
        'password': proxy_match.group(3),
    } if proxy_match else None
    
    all_responses = []
    response_bodies = {}
    
    async def on_response(response):
        url = response.url
        # Skip binary assets
        if any(ext in url.split('?')[0].lower() for ext in ['.png', '.jpg', '.jpeg', '.gif', '.woff', '.css', '.svg', '.mp4', '.webp', '.woff2', '.ttf', '.ico', '.m3u8', '.ts']):
            return
        
        entry = {
            "url": url[:300],
            "status": response.status,
            "content_type": response.headers.get('content-type', ''),
        }
        
        try:
            body = await response.body()
            entry["size"] = len(body)
            text = body.decode('utf-8', errors='replace')
            
            # Check for price-related content
            has_price = '"price"' in text.lower()
            has_nonzero_price = bool(re.search(r'"price"\s*:\s*[1-9]', text))
            has_harga = 'harga' in text.lower()
            has_rp = 'Rp' in text or 'rp ' in text.lower()
            has_product_id = PRODUCT_ID in text
            
            entry["has_price"] = has_price
            entry["has_nonzero_price"] = has_nonzero_price
            entry["has_harga"] = has_harga
            entry["has_rp"] = has_rp
            entry["has_our_product"] = has_product_id
            
            # Save responses that are interesting
            if has_nonzero_price or has_product_id or has_harga or has_rp:
                idx = len(response_bodies) + 1
                fname = f"resp_{idx}_{has_nonzero_price}_{has_product_id}.txt"
                response_bodies[fname] = {
                    "url": url,
                    "body_preview": text[:5000],
                }
                with open(os.path.join(OUTDIR, fname), "w") as f:
                    f.write(f"URL: {url}\n\n{text[:50000]}")
                    
                if has_nonzero_price:
                    print(f"\n  🔥 NON-ZERO PRICE: {url[:100]}")
                    # Try to extract the actual price
                    for m in re.finditer(r'"price"\s*:\s*(\d+)', text):
                        val = int(m.group(1))
                        if val > 0:
                            start = max(0, m.start()-100)
                            end = min(len(text), m.end()+100)
                            print(f"     price={val}, context: {text[start:end][:200]}")
                elif has_product_id:
                    print(f"\n  📦 OUR PRODUCT: {url[:100]}")
                    
        except Exception:
            entry["size"] = 0
            entry["error"] = "body decode failed"
        
        all_responses.append(entry)
    
    mobile_ua = "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, proxy=proxy_config)
        context = await browser.new_context(
            locale='id-ID',
            viewport={'width': 412, 'height': 915},
            user_agent=mobile_ua,
            extra_http_headers={'Accept-Language': 'id-ID,id;q=0.9,en;q=0.5'},
        )
        page = await context.new_page()
        page.on("response", on_response)
        
        # Phase 1: Navigate
        print(f"Phase 1: Navigate to {VIDEO_URL}")
        try:
            await page.goto(VIDEO_URL, wait_until='domcontentloaded', timeout=60000)
        except Exception as e:
            print(f"  Nav: {e}")
        await page.wait_for_timeout(8000)
        
        print(f"  Responses so far: {len(all_responses)}")
        
        # Phase 2: Find and list all product-related elements
        print(f"\nPhase 2: Inspect DOM for product elements...")
        dom_info = await page.evaluate("""() => {
            const results = [];
            
            // All anchors
            document.querySelectorAll('a').forEach(a => {
                const href = a.href || '';
                if (href.includes('shop') || href.includes('product') || href.includes('tokopedia') || 
                    href.includes('ecom') || href.includes('pdp') || href.includes('anchor')) {
                    results.push({
                        tag: 'a',
                        href: href.substring(0, 200),
                        text: (a.textContent || '').trim().substring(0, 100),
                        classes: a.className,
                        dataAttrs: Object.keys(a.dataset).join(','),
                    });
                }
            });
            
            // All elements with ecom/product/anchor classes
            document.querySelectorAll('[class*="ecom"], [class*="Ecom"], [class*="product"], [class*="Product"], [class*="anchor"], [class*="Anchor"], [class*="shop"], [class*="Shop"]').forEach(el => {
                results.push({
                    tag: el.tagName,
                    href: el.href || '',
                    text: (el.textContent || '').trim().substring(0, 100),
                    classes: el.className,
                    dataAttrs: Object.keys(el.dataset).join(','),
                });
            });
            
            return results;
        }""")
        
        print(f"  Found {len(dom_info)} product-related elements:")
        for el in dom_info[:20]:
            print(f"    <{el['tag']} class='{el.get('classes','')[:60]}' href='{el.get('href','')[:80]}'> {el.get('text','')[:60]}")
        
        # Phase 3: Click the product anchor and capture new API calls
        print(f"\nPhase 3: Click product anchor...")
        pre_click_count = len(all_responses)
        
        for sel in ['a[href*="shop-id."]', 'a[href*="tokopedia"]', 'a[href*="pdp"]',
                     '[class*="EcomAnchor"]', '[class*="ecom-anchor"]', '[class*="ecom_anchor"]',
                     '[class*="ProductAnchor"]', '[class*="product-anchor"]']:
            try:
                loc = page.locator(sel)
                cnt = await loc.count()
                if cnt > 0:
                    print(f"  Clicking {sel} ({cnt} elements)...")
                    await loc.first.click(force=True, timeout=3000)
                    await page.wait_for_timeout(6000)
                    
                    new_responses = len(all_responses) - pre_click_count
                    print(f"  New responses after click: {new_responses}")
                    break
            except Exception as e:
                print(f"  Click failed for {sel}: {e}")
        
        # Phase 4: Check if a popup/overlay appeared with product info
        print(f"\nPhase 4: Check for product popup/overlay...")
        overlay_text = await page.evaluate("""() => {
            // Look for overlays, modals, bottom sheets
            const selectors = [
                '[class*="modal"]', '[class*="Modal"]',
                '[class*="overlay"]', '[class*="Overlay"]',
                '[class*="bottom-sheet"]', '[class*="BottomSheet"]',
                '[class*="popup"]', '[class*="Popup"]',
                '[class*="drawer"]', '[class*="Drawer"]',
                '[class*="product-detail"]', '[class*="ProductDetail"]',
                '[class*="ProductCard"]', '[class*="product-card"]',
            ];
            
            let results = [];
            for (const sel of selectors) {
                document.querySelectorAll(sel).forEach(el => {
                    const text = (el.textContent || '').trim();
                    if (text.length > 10 && text.length < 5000) {
                        results.push({
                            selector: sel,
                            visible: el.offsetParent !== null,
                            text: text.substring(0, 500),
                        });
                    }
                });
            }
            return results;
        }""")
        
        for ov in overlay_text:
            print(f"  [{ov['selector']}] visible={ov['visible']}")
            print(f"    Text: {ov['text'][:200]}")
            # Check for price in overlay
            prices = re.findall(r'Rp\s?[\d.]+(?:\.\d{3})*', ov['text'])
            if prices:
                print(f"    ✅ PRICE IN OVERLAY: {prices}")
        
        # Take screenshot
        await page.screenshot(path=os.path.join(OUTDIR, "after_click.png"), full_page=True)
        
        # Phase 5: Also try evaluating window.__NEXT_DATA__ or other hydration
        print(f"\nPhase 5: Check page scripts for price data...")
        script_prices = await page.evaluate("""() => {
            const results = [];
            
            // Check all script tags for price
            document.querySelectorAll('script').forEach(s => {
                const text = s.textContent || '';
                if (text.includes('"price"') && text.length > 100 && text.length < 500000) {
                    // Find price values
                    const matches = text.matchAll(/"price"\s*:\s*(\d+)/g);
                    for (const m of matches) {
                        const val = parseInt(m[1]);
                        if (val > 0) {
                            const start = Math.max(0, m.index - 50);
                            const end = Math.min(text.length, m.index + 100);
                            results.push({
                                value: val,
                                context: text.substring(start, end),
                            });
                        }
                    }
                }
            });
            
            return results;
        }""")
        
        if script_prices:
            print(f"  ✅ Found {len(script_prices)} non-zero prices in page scripts!")
            for sp in script_prices[:10]:
                print(f"    price={sp['value']}: {sp['context'][:150]}")
        else:
            print(f"  No non-zero prices in page scripts")
        
        # Final summary
        print(f"\n" + "="*70)
        print(f"NETWORK CAPTURE SUMMARY")
        print(f"="*70)
        print(f"Total responses: {len(all_responses)}")
        
        with_price = [r for r in all_responses if r.get("has_price")]
        with_nonzero = [r for r in all_responses if r.get("has_nonzero_price")]
        with_product = [r for r in all_responses if r.get("has_our_product")]
        with_rp = [r for r in all_responses if r.get("has_rp")]
        
        print(f"With 'price' field: {len(with_price)}")
        print(f"With NON-ZERO price: {len(with_nonzero)}")
        print(f"With our product ID: {len(with_product)}")
        print(f"With 'Rp': {len(with_rp)}")
        
        # List all responses
        print(f"\nAll responses:")
        for r in all_responses:
            markers = []
            if r.get("has_nonzero_price"): markers.append("🔥PRICE")
            if r.get("has_our_product"): markers.append("📦PRODUCT")
            if r.get("has_rp"): markers.append("💰Rp")
            if r.get("has_price"): markers.append("$price_field")
            
            marker_str = " ".join(markers) if markers else ""
            size = r.get("size", 0)
            print(f"  [{r['status']}] {size:>8}B | {marker_str:30s} | {r['url'][:100]}")
        
        await browser.close()
    
    # Return collected data
    return {
        "total": len(all_responses),
        "with_nonzero_price": len(with_nonzero),
        "with_our_product": len(with_product),
        "with_rp": len(with_rp),
        "bodies_saved": len(response_bodies),
    }


async def main():
    print("=" * 70)
    print(f"PRICE DISCOVERY v15 — Full TikTok Network Capture")
    print(f"Video: {VIDEO_URL}")
    print(f"Product: {PRODUCT_ID}")
    print("=" * 70)
    
    result = await full_capture()
    
    print(f"\n\nFinal result: {json.dumps(result, indent=2)}")
    
    with open(os.path.join(OUTDIR, "summary.json"), "w") as f:
        json.dump(result, f, indent=2)


if __name__ == "__main__":
    asyncio.run(main())
