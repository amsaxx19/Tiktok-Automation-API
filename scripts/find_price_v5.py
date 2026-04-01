#!/usr/bin/env python3
"""
Use patchright (stealth Chromium) to access Tokopedia product page.
Patchright patches Chromium to avoid detection by anti-bot systems.
"""
import asyncio, json, re, os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
PROXY = os.getenv("PROXY_URL", "")

SEO_URL = "https://shop-id.tokopedia.com/pdp/pembersih-busa-sofa-kain-500ml-antibakteri-formula-lembut-wangi-tahan-24-jam-penghilang-noda-kuat/1732773678384055322"
PRODUCT_ID = "1732773678384055322"


async def try_patchright():
    """Use patchright for stealth browsing to Tokopedia."""
    print("=== Patchright Stealth Browser ===", flush=True)
    from patchright.async_api import async_playwright

    proxy_parts = PROXY.replace("http://", "").split("@")
    user_pass = proxy_parts[0].split(":")
    host_port = proxy_parts[1].split(":")
    pw_proxy = {
        "server": f"http://{host_port[0]}:{host_port[1]}",
        "username": user_pass[0],
        "password": user_pass[1],
    }

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            proxy=pw_proxy,
            locale="id-ID",
            viewport={"width": 1366, "height": 768},
        )
        page = await ctx.new_page()

        # Intercept responses for JSON data
        json_hits = []

        async def on_response(response):
            url = response.url
            ct = response.headers.get("content-type", "")
            if response.status == 200 and "json" in ct:
                try:
                    body = await response.text()
                    if len(body) > 100:
                        has_price = "price" in body.lower()
                        json_hits.append({
                            "url": url[:150],
                            "size": len(body),
                            "has_price": has_price,
                        })
                        if has_price:
                            print(f"  💰 JSON with price: {url[:100]} ({len(body)} bytes)", flush=True)
                except:
                    pass

        page.on("response", on_response)

        # Step 1: First visit tokopedia.com to get cookies
        print("  Step 1: Visit tokopedia.com homepage...", flush=True)
        try:
            await page.goto("https://www.tokopedia.com/", wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(3000)
            title = await page.title()
            print(f"  Homepage title: {title}", flush=True)
        except Exception as e:
            print(f"  Homepage error: {e}", flush=True)

        # Step 2: Try the shop-id redirect
        print(f"\n  Step 2: Navigate to product: {SEO_URL[:80]}...", flush=True)
        try:
            resp = await page.goto(SEO_URL, wait_until="domcontentloaded", timeout=25000)
            print(f"  Status: {resp.status if resp else 'None'}", flush=True)
        except Exception as e:
            print(f"  Nav error: {e}", flush=True)

        await page.wait_for_timeout(5000)
        final_url = page.url
        title = await page.title()
        print(f"  Final URL: {final_url}", flush=True)
        print(f"  Title: {title}", flush=True)

        content = await page.content()
        
        if "security" in title.lower() or "verify" in content.lower():
            print("  🔒 Security check detected", flush=True)
            print("  Waiting 10s for possible auto-resolve...", flush=True)
            await page.wait_for_timeout(10000)
            title = await page.title()
            content = await page.content()
            print(f"  After wait - Title: {title}", flush=True)
            
            if "security" in title.lower() or "verify" in content.lower():
                print("  ❌ Still blocked", flush=True)
                await page.screenshot(path="scripts/patchright_tokopedia.png")
                print("  Screenshot saved", flush=True)
            else:
                print("  ✅ Security check passed!", flush=True)
        
        # Check for prices
        rp_prices = re.findall(r"Rp\s?[\d.,]+", content)
        if rp_prices:
            print(f"  💰 Prices in page: {rp_prices[:10]}", flush=True)
        
        # Try LD+JSON
        try:
            ld = await page.evaluate("""() => {
                const scripts = document.querySelectorAll('script[type="application/ld+json"]');
                return Array.from(scripts).map(s => s.textContent);
            }""")
            for l in ld:
                if "price" in l.lower():
                    print(f"  💰 LD+JSON: {l[:500]}", flush=True)
        except:
            pass

        print(f"\n  JSON responses: {len(json_hits)}", flush=True)
        for jh in json_hits[:10]:
            print(f"    {jh}", flush=True)

        await browser.close()


async def try_direct_tokopedia():
    """
    Instead of going through shop-id.tokopedia.com (which has security check),
    try to find the actual Tokopedia product URL and go there directly.
    """
    print("\n\n=== Direct Tokopedia Product Page ===", flush=True)
    from patchright.async_api import async_playwright

    proxy_parts = PROXY.replace("http://", "").split("@")
    user_pass = proxy_parts[0].split(":")
    host_port = proxy_parts[1].split(":")
    pw_proxy = {
        "server": f"http://{host_port[0]}:{host_port[1]}",
        "username": user_pass[0],
        "password": user_pass[1],
    }

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            proxy=pw_proxy,
            locale="id-ID",
            viewport={"width": 1366, "height": 768},
        )
        page = await ctx.new_page()

        # Search for the product on Tokopedia directly
        search_url = "https://www.tokopedia.com/search?q=pembersih+busa+sofa+kain+500ml"
        print(f"  Searching: {search_url[:80]}", flush=True)
        
        try:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=25000)
            await page.wait_for_timeout(5000)
            title = await page.title()
            print(f"  Title: {title}", flush=True)
            
            content = await page.content()
            if "security" in title.lower() or "verify" in content.lower():
                print("  🔒 Security check on search too", flush=True)
            else:
                # Look for price in search results
                rp_prices = re.findall(r"Rp\s?[\d.,]+", content)
                print(f"  Prices found: {rp_prices[:10]}", flush=True)
                
                # Try to find product cards
                cards = await page.query_selector_all("[data-testid='divProductWrapper'], .css-bk6tzz, [class*='product-card']")
                print(f"  Product cards: {len(cards)}", flush=True)
                
                for i, card in enumerate(cards[:5]):
                    text = (await card.text_content() or "").strip()
                    print(f"    Card[{i}]: {text[:200]}", flush=True)
        except Exception as e:
            print(f"  Error: {e}", flush=True)

        await browser.close()


async def try_tokopedia_api_no_proxy():
    """
    Try Tokopedia GraphQL API WITHOUT proxy (direct connection).
    The proxy might be what's triggering the security check.
    """
    print("\n\n=== Tokopedia GraphQL (no proxy) ===", flush=True)
    import httpx

    gql_url = "https://gql.tokopedia.com/graphql/SearchProductQueryV4"
    
    body = [{
        "operationName": "SearchProductQueryV4",
        "variables": {
            "params": "q=pembersih busa sofa kain 500ml&page=1&rows=5&source=search"
        },
        "query": "query SearchProductQueryV4($params:String!){ace_search_product_v4(params:$params){data{products{id name price{text textIdr}imageUrl shop{name city}url badges{title}labelGroups{position title type url}}}}}"
    }]

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Content-Type": "application/json",
        "Origin": "https://www.tokopedia.com",
        "Referer": "https://www.tokopedia.com/search?q=pembersih+busa+sofa+kain+500ml",
        "X-Source": "tokopedia-lite",
        "X-Tkpd-Akamai": "pdpGetLayout",
        "X-Device": "desktop",
    }

    try:
        async with httpx.AsyncClient(timeout=15) as c:
            resp = await c.post(gql_url, json=body, headers=headers)
            print(f"  Status: {resp.status_code}", flush=True)
            print(f"  CT: {resp.headers.get('content-type','')}", flush=True)
            text = resp.text[:3000]
            if "price" in text.lower():
                print("  💰 Has price data!", flush=True)
            print(f"  Body: {text}", flush=True)
    except Exception as e:
        print(f"  Error: {e}", flush=True)

    # Also try with proxy
    print("\n  --- With proxy ---", flush=True)
    try:
        async with httpx.AsyncClient(timeout=15, proxy=PROXY) as c:
            resp = await c.post(gql_url, json=body, headers=headers)
            print(f"  Status: {resp.status_code}", flush=True)
            text = resp.text[:3000]
            if "price" in text.lower():
                print("  💰 Has price data!", flush=True)
            print(f"  Body: {text}", flush=True)
    except Exception as e:
        print(f"  Error: {e}", flush=True)


async def main():
    await try_tokopedia_api_no_proxy()
    await try_patchright()


if __name__ == "__main__":
    asyncio.run(main())
