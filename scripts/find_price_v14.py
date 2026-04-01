"""
Price discovery v14 — Rendered Google Search + Shopping
Use Playwright to render Google Search results and extract prices from rich snippets.
Also try direct Tokopedia with cookie warmup.
"""
import asyncio
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PROXY_URL = os.getenv("PROXY_URL", "http://5sjQhR7dWXPoSuv:gAbLujfGLSP2rWU@178.93.21.156:49644")
PRODUCT_ID = "1732773678384055322"
PRODUCT_TITLE = "Seumnida Sofa Instan Stain Remover Spray"
SEO_URL = "https://shop-id.tokopedia.com/pdp/pembersih-busa-sofa-kain-500ml-antibakteri-formula-lembut-wangi-tahan-24-jam-penghilang-noda-kuat/1732773678384055322"

OUTDIR = os.path.join(os.path.dirname(__file__), "price_final8")
os.makedirs(OUTDIR, exist_ok=True)

DESKTOP_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


async def google_rendered_search():
    """Use Playwright to render Google Search and extract rich snippet prices."""
    print("\n" + "="*70)
    print("APPROACH A: Rendered Google Search for product price")
    print("="*70)
    
    from playwright.async_api import async_playwright
    
    queries = [
        f'"{PRODUCT_TITLE}" harga tokopedia',
        f'{PRODUCT_TITLE} harga',
        f'site:tokopedia.com {PRODUCT_TITLE}',
    ]
    
    async with async_playwright() as p:
        # Use browser WITHOUT proxy for Google (proxy is Indonesian, Google might behave differently)
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            locale='id-ID',
            viewport={'width': 1366, 'height': 768},
            user_agent=DESKTOP_UA,
            extra_http_headers={'Accept-Language': 'id-ID,id;q=0.9,en;q=0.5'},
        )
        page = await context.new_page()
        
        all_prices = []
        
        for i, query in enumerate(queries):
            print(f"\n  Query {i+1}: {query}")
            url = f"https://www.google.com/search?q={query}&hl=id&gl=id&num=10"
            
            try:
                await page.goto(url, wait_until='domcontentloaded', timeout=30000)
                await page.wait_for_timeout(3000)
                
                # Extract visible text from search results
                text_content = await page.evaluate("""() => {
                    let texts = [];
                    // Get all search result snippets
                    document.querySelectorAll('.g, .tF2Cxc, [data-sokoban-container], .MjjYud').forEach(el => {
                        texts.push(el.textContent);
                    });
                    return texts.join('\\n');
                }""")
                
                # Also get all visible text from page
                full_text = await page.evaluate("() => document.body.innerText")
                
                # Find prices in results
                price_matches = re.findall(r'Rp\s?[\d.]+(?:\.\d{3})*', full_text)
                if price_matches:
                    print(f"  ✅ Found prices: {list(set(price_matches))[:10]}")
                    all_prices.extend(price_matches)
                else:
                    print(f"  No prices found in rendered text")
                    # Check if Google showed "Tidak ada hasil" 
                    if "captcha" in full_text.lower() or "unusual traffic" in full_text.lower():
                        print(f"  ⚠️ Google CAPTCHA detected!")
                    
                # Save screenshot and text
                await page.screenshot(path=os.path.join(OUTDIR, f"google_q{i+1}.png"))
                with open(os.path.join(OUTDIR, f"google_text_q{i+1}.txt"), "w") as f:
                    f.write(full_text[:20000])
                    
            except Exception as e:
                print(f"  Error: {e}")
        
        await browser.close()
        
        if all_prices:
            unique = list(set(all_prices))
            print(f"\n  All unique prices found: {unique}")
            return unique
    
    return None


async def google_shopping_rendered():
    """Render Google Shopping tab results."""
    print("\n" + "="*70)
    print("APPROACH B: Rendered Google Shopping")
    print("="*70)
    
    from playwright.async_api import async_playwright
    
    query = f'{PRODUCT_TITLE}'
    url = f"https://www.google.com/search?q={query}&tbm=shop&hl=id&gl=id"
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            locale='id-ID',
            viewport={'width': 1366, 'height': 768},
            user_agent=DESKTOP_UA,
            extra_http_headers={'Accept-Language': 'id-ID,id;q=0.9,en;q=0.5'},
        )
        page = await context.new_page()
        
        print(f"  Query: {query}")
        print(f"  URL: {url}")
        
        try:
            await page.goto(url, wait_until='domcontentloaded', timeout=30000)
            await page.wait_for_timeout(4000)
            
            full_text = await page.evaluate("() => document.body.innerText")
            
            # Find prices
            price_matches = re.findall(r'Rp\s?[\d.]+(?:\.\d{3})*', full_text)
            if price_matches:
                unique = list(set(price_matches))
                print(f"  ✅ Google Shopping prices: {unique[:15]}")
                
                # Try to associate prices with product names
                lines = full_text.split('\n')
                for j, line in enumerate(lines):
                    if 'Rp' in line:
                        context_start = max(0, j-2)
                        context_end = min(len(lines), j+3)
                        ctx = ' | '.join(lines[context_start:context_end])
                        print(f"    Context: {ctx[:150]}")
                
                await page.screenshot(path=os.path.join(OUTDIR, "google_shopping.png"))
                with open(os.path.join(OUTDIR, "google_shopping_text.txt"), "w") as f:
                    f.write(full_text[:20000])
                
                return unique
            else:
                print(f"  No prices found")
                await page.screenshot(path=os.path.join(OUTDIR, "google_shopping.png"))
                with open(os.path.join(OUTDIR, "google_shopping_text.txt"), "w") as f:
                    f.write(full_text[:20000])
                    
        except Exception as e:
            print(f"  Error: {e}")
        
        await browser.close()
    
    return None


async def tiktok_full_network_capture():
    """
    Open TikTok video with Playwright, capture ALL network responses,
    specifically looking for anything with non-zero price.
    Focus on the APIs triggered when clicking the product anchor.
    """
    print("\n" + "="*70)
    print("APPROACH C: TikTok full network capture (focused)")
    print("="*70)
    
    from playwright.async_api import async_playwright
    
    VIDEO_URL = "https://www.tiktok.com/@amosthiosa/video/7485069092459463953"
    
    proxy_match = re.match(r'^(https?://)([^:]+):([^@]+)@([^:]+):(\d+)$', PROXY_URL)
    proxy_config = {
        'server': f"{proxy_match.group(1)}{proxy_match.group(4)}:{proxy_match.group(5)}",
        'username': proxy_match.group(2),
        'password': proxy_match.group(3),
    } if proxy_match else None
    
    interesting_responses = []
    
    async def on_response(response):
        url = response.url
        # Skip static assets
        if any(ext in url for ext in ['.png', '.jpg', '.jpeg', '.gif', '.woff', '.css', '.svg', '.mp4', '.webp', '.woff2', '.ttf', '.ico']):
            return
        
        # Focus on API endpoints
        if not any(kw in url.lower() for kw in ['api', 'product', 'price', 'ecommerce', 'shop', 'reflow', 'item', 'detail', 'oec', 'anchor']):
            return
            
        try:
            if response.status == 200:
                ct = response.headers.get('content-type', '')
                if 'json' in ct or 'text' in ct or 'javascript' in ct:
                    body = await response.body()
                    text = body.decode('utf-8', errors='replace')
                    
                    # Save anything interesting
                    interesting_responses.append({
                        "url": url[:200],
                        "status": response.status,
                        "size": len(text),
                        "has_price_field": '"price"' in text,
                        "has_nonzero_price": bool(re.search(r'"price"\s*:\s*[1-9]', text)),
                    })
                    
                    # If has non-zero price, save full response
                    if re.search(r'"price"\s*:\s*[1-9]', text):
                        fname = f"nonzero_price_{len(interesting_responses)}.json"
                        with open(os.path.join(OUTDIR, fname), "w") as f:
                            f.write(text[:100000])
                        print(f"  🔥 NON-ZERO PRICE in: {url[:100]}")
                        print(f"     Saved to {fname}")
        except:
            pass
    
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
        
        print(f"  Navigating to {VIDEO_URL}")
        try:
            await page.goto(VIDEO_URL, wait_until='domcontentloaded', timeout=60000)
        except Exception as e:
            print(f"  Nav: {e}")
        
        await page.wait_for_timeout(6000)
        
        # Click on product anchors
        print("  Clicking product elements...")
        for sel in ['a[href*="shop-id."]', '[class*="EcomAnchor"]', '[class*="ecom"]',
                     '[class*="product"]', '[class*="anchor"]', 'a[href*="tokopedia"]']:
            try:
                loc = page.locator(sel)
                cnt = await loc.count()
                if cnt > 0:
                    print(f"    Clicking {sel} ({cnt} elements)...")
                    await loc.first.click(force=True, timeout=3000)
                    await page.wait_for_timeout(4000)
                    break
            except:
                pass
        
        # Wait for more API calls
        await page.wait_for_timeout(5000)
        
        # Summary of API calls
        print(f"\n  Total API responses captured: {len(interesting_responses)}")
        price_fields = [r for r in interesting_responses if r["has_price_field"]]
        nonzero = [r for r in interesting_responses if r["has_nonzero_price"]]
        
        print(f"  With 'price' field: {len(price_fields)}")
        print(f"  With NON-ZERO price: {len(nonzero)}")
        
        for r in interesting_responses:
            marker = "🔥" if r["has_nonzero_price"] else ("💰" if r["has_price_field"] else "  ")
            print(f"    {marker} [{r['status']}] {r['size']:>8}B | {r['url'][:100]}")
        
        await browser.close()
    
    return nonzero if nonzero else None


async def tokopedia_via_playwright_with_cookies():
    """
    Try Tokopedia product page with Playwright, using proxy,
    with cookie warmup and randomized delays to appear more human.
    """
    print("\n" + "="*70)
    print("APPROACH D: Tokopedia via Playwright with cookie warmup")
    print("="*70)
    
    from playwright.async_api import async_playwright
    
    proxy_match = re.match(r'^(https?://)([^:]+):([^@]+)@([^:]+):(\d+)$', PROXY_URL)
    proxy_config = {
        'server': f"{proxy_match.group(1)}{proxy_match.group(4)}:{proxy_match.group(5)}",
        'username': proxy_match.group(2),
        'password': proxy_match.group(3),
    } if proxy_match else None
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            proxy=proxy_config,
            args=['--disable-blink-features=AutomationControlled']
        )
        context = await browser.new_context(
            locale='id-ID',
            timezone_id='Asia/Jakarta',
            viewport={'width': 1366, 'height': 768},
            user_agent=DESKTOP_UA,
            extra_http_headers={'Accept-Language': 'id-ID,id;q=0.9,en;q=0.5'},
        )
        
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            delete navigator.__proto__.webdriver;
        """)
        
        page = await context.new_page()
        
        # Step 1: Visit tokopedia homepage
        print("  Step 1: Warming up with tokopedia.com...")
        try:
            await page.goto("https://www.tokopedia.com/", wait_until='domcontentloaded', timeout=25000)
            await page.wait_for_timeout(3000)
            title = await page.title()
            print(f"  Homepage title: {title}")
            
            if "security" in title.lower():
                print(f"  ❌ Security check on homepage already!")
                await page.screenshot(path=os.path.join(OUTDIR, "tokopedia_homepage.png"))
                await browser.close()
                return None
                
        except Exception as e:
            print(f"  Homepage error: {e}")
            # Try without waiting for full load
        
        # Step 2: Navigate to product
        print(f"  Step 2: Navigating to product page...")
        try:
            await page.goto(SEO_URL, wait_until='domcontentloaded', timeout=25000)
            await page.wait_for_timeout(5000)
            
            title = await page.title()
            html = await page.content()
            print(f"  Product page title: {title}")
            print(f"  HTML length: {len(html)}")
            
            await page.screenshot(path=os.path.join(OUTDIR, "tokopedia_product.png"))
            
            if "security" in title.lower() or "security check" in html.lower()[:5000]:
                print(f"  ❌ Security check on product page")
                
                # Try to get visible text anyway
                visible_text = await page.evaluate("() => document.body.innerText")
                print(f"  Visible text: {visible_text[:300]}")
                
                with open(os.path.join(OUTDIR, "tokopedia_product.html"), "w") as f:
                    f.write(html)
            else:
                # Maybe we got through!
                visible_text = await page.evaluate("() => document.body.innerText")
                prices = re.findall(r'Rp\s?[\d.]+(?:\.\d{3})*', visible_text)
                if prices:
                    print(f"  ✅ PRICES FOUND: {prices[:10]}")
                    with open(os.path.join(OUTDIR, "tokopedia_success.html"), "w") as f:
                        f.write(html)
                    await browser.close()
                    return prices
                    
                print(f"  Page text: {visible_text[:500]}")
                
        except Exception as e:
            print(f"  Product page error: {e}")
        
        await browser.close()
    
    return None


async def main():
    print("=" * 70)
    print(f"PRICE DISCOVERY v14 — Rendered approaches")
    print(f"Product: {PRODUCT_TITLE}")
    print(f"ID: {PRODUCT_ID}")
    print("=" * 70)
    
    results = {}
    
    approaches = [
        ("google_search_rendered", google_rendered_search),
        ("google_shopping_rendered", google_shopping_rendered),
        ("tiktok_network_capture", tiktok_full_network_capture),
        ("tokopedia_playwright", tokopedia_via_playwright_with_cookies),
    ]
    
    for name, func in approaches:
        try:
            result = await func()
            results[name] = result
            if result:
                print(f"\n✅ {name} SUCCEEDED!")
        except Exception as e:
            print(f"\n❌ {name} FAILED: {e}")
            import traceback
            traceback.print_exc()
            results[name] = None
    
    # Summary
    print("\n" + "=" * 70)
    print("FINAL SUMMARY")
    print("=" * 70)
    for name, result in results.items():
        status = "✅" if result else "❌"
        preview = str(result)[:120] if result else "None"
        print(f"  {status} {name}: {preview}")
    
    # Save
    with open(os.path.join(OUTDIR, "summary.json"), "w") as f:
        json.dump({k: str(v)[:500] for k, v in results.items()}, f, indent=2)


if __name__ == "__main__":
    asyncio.run(main())
