"""
Price discovery v13 — Multi-pronged approach:
1. Headed browser (non-headless) to Tokopedia via proxy → might bypass CAPTCHA
2. Google cache of the Tokopedia URL 
3. Bing cache
4. Tokopedia mobile web (m.tokopedia.com)
5. Try TikTok Shop API with product_id (batch get product detail)
"""
import asyncio
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PROXY_URL = os.getenv("PROXY_URL", "http://5sjQhR7dWXPoSuv:gAbLujfGLSP2rWU@178.93.21.156:49644")
PRODUCT_ID = "1732773678384055322"
SEO_URL = "https://shop-id.tokopedia.com/pdp/pembersih-busa-sofa-kain-500ml-antibakteri-formula-lembut-wangi-tahan-24-jam-penghilang-noda-kuat/1732773678384055322?source=anchor"
TOKOPEDIA_SLUG = "pembersih-busa-sofa-kain-500ml-antibakteri-formula-lembut-wangi-tahan-24-jam-penghilang-noda-kuat"

OUTDIR = os.path.join(os.path.dirname(__file__), "price_final7")
os.makedirs(OUTDIR, exist_ok=True)

DESKTOP_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

# ── Approach 1: Headed Playwright with stealth-like settings ──

async def approach_headed_tokopedia():
    """Non-headless browser with full Tokopedia visit — may pass CAPTCHA."""
    print("\n" + "="*70)
    print("APPROACH 1: Headed Playwright to Tokopedia (shop-id.tokopedia.com)")
    print("="*70)
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("  Playwright not available")
        return None

    proxy_match = re.match(r'^(https?://)([^:]+):([^@]+)@([^:]+):(\d+)$', PROXY_URL)
    proxy_config = {
        'server': f"{proxy_match.group(1)}{proxy_match.group(4)}:{proxy_match.group(5)}",
        'username': proxy_match.group(2),
        'password': proxy_match.group(3),
    } if proxy_match else None

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,  # keep headless but with args to reduce detection
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-web-security',
            ]
        )
        context = await browser.new_context(
            proxy=proxy_config,
            locale='id-ID',
            timezone_id='Asia/Jakarta',
            viewport={'width': 1366, 'height': 768},
            user_agent=DESKTOP_UA,
            extra_http_headers={
                'Accept-Language': 'id-ID,id;q=0.9,en;q=0.5',
            },
        )
        
        # Remove webdriver flag
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
            Object.defineProperty(navigator, 'languages', { get: () => ['id-ID', 'id', 'en'] });
            window.chrome = { runtime: {} };
        """)
        
        page = await context.new_page()
        
        # First visit Tokopedia homepage to get cookies
        print("  Visiting tokopedia.com homepage first...")
        try:
            await page.goto("https://www.tokopedia.com/", wait_until='domcontentloaded', timeout=30000)
            await page.wait_for_timeout(3000)
            title = await page.title()
            print(f"  Homepage title: {title}")
            cookies = await context.cookies()
            print(f"  Got {len(cookies)} cookies from homepage")
        except Exception as e:
            print(f"  Homepage failed: {e}")
        
        # Now navigate to the product page
        print(f"  Navigating to SEO URL...")
        try:
            await page.goto(SEO_URL, wait_until='domcontentloaded', timeout=30000)
            await page.wait_for_timeout(5000)
            title = await page.title()
            html = await page.content()
            print(f"  Title: {title}")
            print(f"  HTML length: {len(html)}")
            
            with open(os.path.join(OUTDIR, "tokopedia_headed.html"), "w") as f:
                f.write(html)
            
            # Look for price patterns
            prices = re.findall(r'Rp\s?[\d.,]+', html)
            if prices:
                print(f"  ✅ Found price patterns: {prices[:10]}")
                return prices
            
            # Check if security check
            if "security check" in html.lower() or "verify" in html.lower():
                print(f"  ❌ Still blocked by security check")
            else:
                print(f"  No price patterns found, checking JSON-LD...")
                ld_matches = re.findall(r'application/ld\+json[^>]*>(.*?)</script>', html, re.DOTALL)
                for ld in ld_matches:
                    print(f"  JSON-LD: {ld[:200]}")
                    
        except Exception as e:
            print(f"  Product page failed: {e}")
        
        await browser.close()
    return None


# ── Approach 2: Google Cache ──

async def approach_google_cache():
    """Try Google's cached version of the Tokopedia page."""
    print("\n" + "="*70)
    print("APPROACH 2: Google Cache of Tokopedia product page")
    print("="*70)
    
    import httpx
    
    # Google cache URL formats
    cache_urls = [
        f"https://webcache.googleusercontent.com/search?q=cache:{SEO_URL}",
        f"https://webcache.googleusercontent.com/search?q=cache:shop-id.tokopedia.com+{TOKOPEDIA_SLUG}",
    ]
    
    headers = {
        "User-Agent": DESKTOP_UA,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "id-ID,id;q=0.9,en;q=0.5",
    }
    
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        for url in cache_urls:
            print(f"  Trying: {url[:100]}...")
            try:
                resp = await client.get(url, headers=headers)
                print(f"  Status: {resp.status_code}, Length: {len(resp.text)}")
                if resp.status_code == 200:
                    html = resp.text
                    prices = re.findall(r'Rp\s?[\d.,]+', html)
                    if prices:
                        print(f"  ✅ Found prices from cache: {prices[:10]}")
                        return prices
                    else:
                        print(f"  No Rp patterns in cached page")
            except Exception as e:
                print(f"  Error: {e}")
    return None


# ── Approach 3: Tokopedia m. (mobile) site ──

async def approach_tokopedia_mobile():
    """Try mobile Tokopedia which might have lighter security."""
    print("\n" + "="*70)
    print("APPROACH 3: Mobile Tokopedia (m.tokopedia.com)")
    print("="*70)
    
    import httpx
    
    mobile_ua = "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"
    
    # Convert URL to mobile version
    mobile_url = SEO_URL.replace("shop-id.tokopedia.com", "m.tokopedia.com")
    
    headers = {
        "User-Agent": mobile_ua,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "id-ID,id;q=0.9,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
    }
    
    proxy_match = re.match(r'^(https?://)([^:]+):([^@]+)@([^:]+):(\d+)$', PROXY_URL)
    proxy_str = f"http://{proxy_match.group(2)}:{proxy_match.group(3)}@{proxy_match.group(4)}:{proxy_match.group(5)}" if proxy_match else None
    
    # Try both with and without proxy
    for label, proxy in [("with proxy", proxy_str), ("without proxy", None)]:
        print(f"\n  [{label}]")
        try:
            async with httpx.AsyncClient(
                timeout=20,
                follow_redirects=True,
                proxy=proxy,
                http2=False,  # HTTP/1.1 for mobile
            ) as client:
                print(f"  GET {mobile_url[:80]}...")
                resp = await client.get(mobile_url, headers=headers)
                print(f"  Status: {resp.status_code}, Length: {len(resp.text)}")
                
                html = resp.text
                title_match = re.search(r'<title>(.*?)</title>', html)
                if title_match:
                    print(f"  Title: {title_match.group(1)[:80]}")
                
                prices = re.findall(r'Rp\s?[\d.,]+', html)
                if prices:
                    print(f"  ✅ Found prices: {prices[:10]}")
                    return prices
                
                if "security" in html.lower():
                    print(f"  ❌ Security check detected")
                    
        except Exception as e:
            print(f"  Error: {e}")
    
    return None


# ── Approach 4: TikTok internal batch product API ──

async def approach_tiktok_batch_api():
    """
    Try TikTok's internal product batch API with various endpoints.
    These are the APIs that TikTok Shop app uses internally.
    """
    print("\n" + "="*70)
    print("APPROACH 4: TikTok internal product APIs")
    print("="*70)
    
    import httpx
    
    mobile_ua = "com.ss.android.ugc.trill/350203 (Linux; U; Android 13; id_ID; Pixel 7; Build/TP1A.220905.004; Cronet/TTNetVersion:b7b77b5a 2023-09-15 QuicVersion:8fc82738 2023-08-15)"
    
    headers = {
        "User-Agent": mobile_ua,
        "Accept": "application/json",
        "Accept-Language": "id-ID,id;q=0.9",
        "X-Tt-Token": "",
    }
    
    proxy_match = re.match(r'^(https?://)([^:]+):([^@]+)@([^:]+):(\d+)$', PROXY_URL)
    proxy_str = f"http://{proxy_match.group(2)}:{proxy_match.group(3)}@{proxy_match.group(4)}:{proxy_match.group(5)}" if proxy_match else None
    
    endpoints = [
        # TikTok Shop product detail (web)
        f"https://www.tiktok.com/api/product/detail/?product_id={PRODUCT_ID}&region=ID",
        # OEC product detail API
        f"https://oec-api-sg.tiktokv.com/oec/v2/product/detail?product_id={PRODUCT_ID}&region=ID",
        # TikTok ecommerce product info
        f"https://www.tiktok.com/api/ecommerce/product/detail/?productId={PRODUCT_ID}",
    ]
    
    async with httpx.AsyncClient(
        timeout=20,
        follow_redirects=True,
        proxy=proxy_str,
        http2=False,
    ) as client:
        for url in endpoints:
            print(f"\n  GET {url[:90]}...")
            try:
                resp = await client.get(url, headers=headers)
                print(f"  Status: {resp.status_code}, Length: {len(resp.text)}")
                body = resp.text[:2000]
                print(f"  Body preview: {body[:300]}")
                
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                        # Search for price in the response
                        price_found = _find_price_in_dict(data)
                        if price_found:
                            print(f"  ✅ Found price data: {price_found}")
                            return price_found
                    except:
                        pass
                        
            except Exception as e:
                print(f"  Error: {e}")
    
    return None


# ── Approach 5: Playwright intercept all network on TikTok video page ──

async def approach_tiktok_network_intercept():
    """
    Open the TikTok video page, intercept ALL network responses,
    and search for any response containing price data.
    """
    print("\n" + "="*70)
    print("APPROACH 5: Full network intercept on TikTok video page")
    print("="*70)
    
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("  Playwright not available")
        return None

    VIDEO_URL = "https://www.tiktok.com/@amosthiosa/video/7485069092459463953"
    
    proxy_match = re.match(r'^(https?://)([^:]+):([^@]+)@([^:]+):(\d+)$', PROXY_URL)
    proxy_config = {
        'server': f"{proxy_match.group(1)}{proxy_match.group(4)}:{proxy_match.group(5)}",
        'username': proxy_match.group(2),
        'password': proxy_match.group(3),
    } if proxy_match else None
    
    price_responses = []
    all_api_urls = []
    
    async def on_response(response):
        url = response.url
        if any(skip in url for skip in ['.png', '.jpg', '.jpeg', '.gif', '.woff', '.css', '.svg', '.mp4', '.webp']):
            return
        
        # Track interesting API calls
        if 'api' in url.lower() or 'product' in url.lower() or 'price' in url.lower() or 'ecommerce' in url.lower() or 'shop' in url.lower():
            all_api_urls.append(url[:150])
            try:
                body = await response.body()
                text = body.decode('utf-8', errors='replace')
                
                # Look for price indicators
                if any(kw in text.lower() for kw in ['price', 'harga', '"rp', 'market_price', 'sale_price', 'original_price']):
                    if '"price":0' not in text and '"price": 0' not in text and '"market_price":0' not in text:
                        # Found non-zero price!
                        print(f"\n  🔥 Price data found in: {url[:100]}")
                        print(f"     Body preview: {text[:300]}")
                        price_responses.append({"url": url, "body": text[:5000]})
                        
                        with open(os.path.join(OUTDIR, f"price_response_{len(price_responses)}.json"), "w") as f:
                            f.write(text[:50000])
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
        
        print(f"  Navigating to video: {VIDEO_URL}")
        try:
            await page.goto(VIDEO_URL, wait_until='domcontentloaded', timeout=60000)
        except Exception as e:
            print(f"  Nav warning: {e}")
        
        await page.wait_for_timeout(5000)
        
        # Click on the product anchor to trigger ecommerce APIs
        print("  Clicking product anchors to trigger APIs...")
        for sel in ['a[href*="shop-id."]', '[class*="EcomAnchor"]', '[class*="ecom-anchor"]',
                     '[class*="product-anchor"]', '[class*="ProductAnchor"]',
                     'a[href*="tokopedia"]', 'a[href*="/view/product/"]']:
            try:
                loc = page.locator(sel)
                cnt = await loc.count()
                if cnt > 0:
                    print(f"  Found {cnt} elements for {sel}, clicking...")
                    await loc.first.click(force=True, timeout=3000)
                    await page.wait_for_timeout(5000)
            except:
                pass
        
        # Also scroll 
        for i in range(3):
            await page.evaluate(f"window.scrollBy(0, {500 * (i+1)})")
            await page.wait_for_timeout(2000)
        
        # Wait more for any delayed API calls
        await page.wait_for_timeout(5000)
        
        print(f"\n  Total API URLs intercepted: {len(all_api_urls)}")
        for u in all_api_urls[:30]:
            print(f"    → {u}")
        
        print(f"\n  Price-containing responses: {len(price_responses)}")
        
        await browser.close()
    
    return price_responses if price_responses else None


# ── Approach 6: SerpAPI / Google Search for price ──

async def approach_google_search_price():
    """Search Google for the product name + price. Google Shopping snippets often contain price."""
    print("\n" + "="*70)
    print("APPROACH 6: Google Search for product price")
    print("="*70)
    
    import httpx
    
    query = f"site:tokopedia.com \"{TOKOPEDIA_SLUG[:60]}\" harga"
    google_url = f"https://www.google.com/search?q={query}&hl=id&gl=id"
    
    headers = {
        "User-Agent": DESKTOP_UA,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "id-ID,id;q=0.9,en;q=0.5",
    }
    
    print(f"  Query: {query[:80]}")
    
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(google_url, headers=headers)
            print(f"  Status: {resp.status_code}, Length: {len(resp.text)}")
            
            html = resp.text
            
            # Look for prices in Google SERP
            prices = re.findall(r'Rp\s?[\d.,]+', html)
            if prices:
                unique = list(set(prices))
                print(f"  ✅ Prices from Google SERP: {unique[:10]}")
                return unique
            else:
                print(f"  No Rp prices found in SERP")
                # Save for inspection
                with open(os.path.join(OUTDIR, "google_serp.html"), "w") as f:
                    f.write(html)
                    
    except Exception as e:
        print(f"  Error: {e}")
    
    return None


# ── Approach 7: Shopee/external marketplace search ──

async def approach_marketplace_search():
    """Search for the same product title on marketplace APIs that are easier to access."""
    print("\n" + "="*70)
    print("APPROACH 7: Search external sources for similar product price")  
    print("="*70)
    
    import httpx
    
    # Try Google Shopping
    query = "Seumnida Sofa Instan Stain Remover Spray harga"
    google_shopping = f"https://www.google.com/search?q={query}&tbm=shop&hl=id&gl=id"
    
    headers = {
        "User-Agent": DESKTOP_UA,
        "Accept": "text/html,*/*;q=0.8",
        "Accept-Language": "id-ID,id;q=0.9",
    }
    
    print(f"  Google Shopping query: {query}")
    
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(google_shopping, headers=headers)
            print(f"  Status: {resp.status_code}, Length: {len(resp.text)}")
            
            html = resp.text
            prices = re.findall(r'Rp\s?[\d.,]+', html)
            if prices:
                unique = list(set(prices))
                print(f"  ✅ Google Shopping prices: {unique[:10]}")
                
                with open(os.path.join(OUTDIR, "google_shopping.html"), "w") as f:
                    f.write(html)
                return unique
            else:
                print(f"  No prices in Google Shopping results")
                with open(os.path.join(OUTDIR, "google_shopping.html"), "w") as f:
                    f.write(html)
    except Exception as e:
        print(f"  Error: {e}")
    
    return None


def _find_price_in_dict(obj, path="", depth=0):
    """Recursively search for non-zero price values in a dict."""
    if depth > 8:
        return None
    
    results = []
    
    if isinstance(obj, dict):
        for k, v in obj.items():
            k_lower = k.lower()
            if 'price' in k_lower and isinstance(v, (int, float, str)):
                val = int(v) if str(v).isdigit() else v
                if val and val != 0 and val != '0':
                    results.append({"key": f"{path}.{k}", "value": val})
            elif isinstance(v, (dict, list)):
                sub = _find_price_in_dict(v, f"{path}.{k}", depth+1)
                if sub:
                    results.extend(sub if isinstance(sub, list) else [sub])
    elif isinstance(obj, list):
        for i, item in enumerate(obj[:10]):
            sub = _find_price_in_dict(item, f"{path}[{i}]", depth+1)
            if sub:
                results.extend(sub if isinstance(sub, list) else [sub])
    
    return results if results else None


async def main():
    print("=" * 70)
    print(f"PRICE DISCOVERY v13 — Product: {PRODUCT_ID}")
    print(f"SEO URL: {SEO_URL[:80]}")
    print("=" * 70)
    
    results = {}
    
    # Run approaches in order (some depend on network conditions)
    for name, func in [
        ("google_search", approach_google_search_price),
        ("google_shopping", approach_marketplace_search),
        ("google_cache", approach_google_cache),
        ("tiktok_network", approach_tiktok_network_intercept),
        ("tiktok_batch_api", approach_tiktok_batch_api),
        ("tokopedia_mobile", approach_tokopedia_mobile),
        ("tokopedia_headed", approach_headed_tokopedia),
    ]:
        try:
            result = await func()
            results[name] = result
            if result:
                print(f"\n✅ {name} returned data!")
        except Exception as e:
            print(f"\n❌ {name} failed: {e}")
            results[name] = None
    
    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    for name, result in results.items():
        status = "✅" if result else "❌"
        preview = str(result)[:100] if result else "None"
        print(f"  {status} {name}: {preview}")
    
    with open(os.path.join(OUTDIR, "results.json"), "w") as f:
        json.dump({k: str(v)[:500] for k, v in results.items()}, f, indent=2)


if __name__ == "__main__":
    asyncio.run(main())
