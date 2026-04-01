"""
Approach: Visit Tokopedia homepage first to get proper cookies/session,
then navigate to the product page. Many anti-bot systems require proper
session initiation.

Also try: regular tokopedia.com product URL (not shop-id.tokopedia.com)
"""
import asyncio
import json
import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PRODUCT_NAME = "Pena uji kualitas air TDS profesional"
PRODUCT_SLUG = "pena-uji-kualitas-air-tds-profesional-alat-uji-minuman-presisi-tinggi"
PRODUCT_ID = "1731154567051904037"

# The original URL from TikTok Shop
SHOP_ID_URL = f"https://shop-id.tokopedia.com/pdp/{PRODUCT_SLUG}/{PRODUCT_ID}?source=anchor"


async def main():
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
            ],
        )

        # Method A: Session warming + product page
        print("🔥 Method A: Session warming on Tokopedia homepage")
        context = await browser.new_context(
            locale='id-ID',
            timezone_id='Asia/Jakarta',
            viewport={'width': 1366, 'height': 768},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            extra_http_headers={'Accept-Language': 'id-ID,id;q=0.9'},
        )
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            delete navigator.__proto__.webdriver;
            // Override permissions
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );
        """)
        
        page = await context.new_page()

        # Step 1: Visit Tokopedia homepage
        print("  Step 1: Loading tokopedia.com homepage...")
        try:
            await page.goto("https://www.tokopedia.com/", wait_until='domcontentloaded', timeout=15000)
            await page.wait_for_timeout(3000)
            title = await page.title()
            print(f"  Homepage title: {title[:80]}")
            cookies = await context.cookies()
            print(f"  Cookies after homepage: {len(cookies)}")
        except Exception as e:
            print(f"  Homepage failed: {e}")

        # Step 2: Search for the product
        print("\n  Step 2: Searching for product...")
        search_url = f"https://www.tokopedia.com/search?q={PRODUCT_NAME[:40].replace(' ', '+')}"
        try:
            await page.goto(search_url, wait_until='domcontentloaded', timeout=15000)
            await page.wait_for_timeout(3000)
            title = await page.title()
            print(f"  Search title: {title[:80]}")
            
            if "security" in title.lower():
                print("  ❌ Security check on search")
            else:
                # Extract prices from search results
                data = await page.evaluate("""() => {
                    const results = [];
                    // Tokopedia search results
                    document.querySelectorAll('[data-testid="divSRPContentProducts"] a').forEach(el => {
                        const name = el.querySelector('[data-testid="spnSRPProdName"]')?.textContent || '';
                        const price = el.querySelector('[data-testid="spnSRPProdPrice"]')?.textContent || '';
                        const shop = el.querySelector('[data-testid="spnSRPProdShop"]')?.textContent || '';
                        if (name || price) results.push({name: name.substring(0, 60), price, shop});
                    });
                    
                    // Fallback: all price-like elements
                    const allPrices = [];
                    document.querySelectorAll('*').forEach(el => {
                        const t = el.textContent?.trim() || '';
                        if (/^Rp[\\s.]?[\\d.,]+$/.test(t) && t.length < 20) {
                            allPrices.push(t);
                        }
                    });
                    
                    return {
                        results: results.slice(0, 10),
                        allPrices: [...new Set(allPrices)].slice(0, 20),
                        bodyText: document.body.innerText?.substring(0, 1000) || '',
                    };
                }""")
                
                if data.get('results'):
                    print(f"  Found {len(data['results'])} search results:")
                    for r in data['results'][:5]:
                        print(f"    {r['name'][:50]} | {r['price']} | {r['shop']}")
                
                if data.get('allPrices'):
                    print(f"  All Rp prices on page: {data['allPrices'][:10]}")
                
                if not data.get('results') and not data.get('allPrices'):
                    print(f"  Body text: {data.get('bodyText', '')[:300]}")
        except Exception as e:
            print(f"  Search failed: {e}")

        # Step 3: Try direct navigation to product with session
        print("\n  Step 3: Navigating to product page with session...")
        try:
            await page.goto(SHOP_ID_URL, wait_until='domcontentloaded', timeout=15000)
            await page.wait_for_timeout(3000)
            title = await page.title()
            current_url = page.url
            print(f"  Title: {title[:80]}")
            print(f"  URL: {current_url[:120]}")
            
            if "security" in title.lower():
                print("  ❌ Security check on product page (even with session)")
            else:
                data = await page.evaluate("""() => {
                    const result = {};
                    const priceEl = document.querySelector('[data-testid="lblPDPDetailProductPrice"]');
                    if (priceEl) result.price = priceEl.textContent;
                    
                    const rpMatches = (document.body.innerText || '').match(/Rp[\\s.]?[\\d.,]+/g) || [];
                    result.rp_prices = rpMatches.slice(0, 10);
                    
                    return result;
                }""")
                print(f"  Price: {data.get('price')}")
                print(f"  Rp prices: {data.get('rp_prices')}")
        except Exception as e:
            print(f"  Product page failed: {e}")

        await context.close()
        
        # Method B: Try with Indonesia proxy
        print("\n\n🇮🇩 Method B: With Indonesia proxy + session warming")
        proxy = {
            "server": "http://178.93.21.156:49644",
            "username": "5sjQhR7dWXPoSuv",
            "password": "gAbLujfGLSP2rWU",
        }
        context2 = await browser.new_context(
            proxy=proxy,
            locale='id-ID',
            timezone_id='Asia/Jakarta',
            viewport={'width': 1366, 'height': 768},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            extra_http_headers={'Accept-Language': 'id-ID,id;q=0.9'},
        )
        await context2.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)
        
        page2 = await context2.new_page()
        
        # Step 1: Warm session
        print("  Step 1: Loading homepage with proxy...")
        try:
            await page2.goto("https://www.tokopedia.com/", wait_until='domcontentloaded', timeout=20000)
            await page2.wait_for_timeout(3000)
            title = await page2.title()
            print(f"  Title: {title[:80]}")
            cookies = await context2.cookies()
            print(f"  Cookies: {len(cookies)}")
        except Exception as e:
            print(f"  Homepage: {e}")

        # Step 2: Product page
        print("\n  Step 2: Product page with proxy + session...")
        try:
            await page2.goto(SHOP_ID_URL, wait_until='domcontentloaded', timeout=20000)
            await page2.wait_for_timeout(3000)
            title = await page2.title()
            print(f"  Title: {title[:80]}")
            
            if "security" in title.lower():
                print("  ❌ Security check")
                
                # Try to solve: wait longer, scroll
                await page2.wait_for_timeout(5000)
                title2 = await page2.title()
                print(f"  After 5s wait: {title2[:80]}")
            else:
                data = await page2.evaluate("""() => {
                    const rpMatches = (document.body.innerText || '').match(/Rp[\\s.]?[\\d.,]+/g) || [];
                    return { rp_prices: rpMatches.slice(0, 10) };
                }""")
                print(f"  Rp prices: {data.get('rp_prices')}")
        except Exception as e:
            print(f"  Error: {e}")
        
        await context2.close()
        await browser.close()

asyncio.run(main())
