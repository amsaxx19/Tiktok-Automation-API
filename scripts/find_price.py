#!/usr/bin/env python3
"""Try multiple approaches to get product price data."""
import httpx, asyncio, json, os, re
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
PROXY = os.getenv("PROXY_URL", "")

product_id = "1732773678384055322"
seo_url = f"https://shop-id.tokopedia.com/pdp/pembersih-busa-sofa-kain-500ml-antibakteri-formula-lembut-wangi-tahan-24-jam-penghilang-noda-kuat/{product_id}"


async def test_googlebot():
    print("=== Test 1: Googlebot full response ===", flush=True)
    async with httpx.AsyncClient(timeout=15, follow_redirects=True, proxy=PROXY) as c:
        resp = await c.get(seo_url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
            "Accept": "text/html,application/json",
        })
        print(f"Status: {resp.status_code}, CT: {resp.headers.get('content-type','')}", flush=True)
        print(f"Body: {resp.text[:3000]}", flush=True)


async def test_tokopedia_gql():
    print("\n=== Test 2: Tokopedia GraphQL ===", flush=True)
    gql_url = "https://gql.tokopedia.com/graphql/PDPGetLayoutQuery"
    query = """query PDPGetLayoutQuery($shopDomain:String,$productKey:String,$layoutID:String,$apiVersion:Float) {
        pdpGetLayout(shopDomain:$shopDomain,productKey:$productKey,layoutID:$layoutID,apiVersion:$apiVersion) {
            requestID name
            basicInfo { id shopID alias url condition }
        }
    }"""
    gql_body = [{
        "operationName": "PDPGetLayoutQuery",
        "variables": {
            "shopDomain": "",
            "productKey": product_id,
            "layoutID": "",
            "apiVersion": 1,
        },
        "query": query
    }]
    try:
        async with httpx.AsyncClient(timeout=15, proxy=PROXY) as c:
            resp = await c.post(gql_url, json=gql_body, headers={
                "User-Agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Origin": "https://www.tokopedia.com",
                "Referer": "https://www.tokopedia.com/",
                "X-Source": "tokopedia-lite",
                "X-Device": "mobile",
                "X-Tkpd-Akamai": "pdpGetLayout",
            })
            print(f"Status: {resp.status_code}, CT: {resp.headers.get('content-type','')}", flush=True)
            print(f"Body: {resp.text[:3000]}", flush=True)
    except Exception as e:
        print(f"Error: {e}", flush=True)


async def test_tokopedia_pdp():
    print("\n=== Test 3: Tokopedia PDP direct ===", flush=True)
    # Try the actual Tokopedia product URL derived from seo_url
    # seo_url goes to shop-id.tokopedia.com which redirects
    url = f"https://www.tokopedia.com/find/{product_id}"
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True, proxy=PROXY) as c:
            resp = await c.get(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
                "Accept": "text/html",
            })
            print(f"Status: {resp.status_code}, CT: {resp.headers.get('content-type','')}", flush=True)
            if "price" in resp.text.lower() or "Rp" in resp.text:
                prices = re.findall(r"Rp\s?[\d.,]+", resp.text)
                print(f"💰 Found prices: {prices[:10]}", flush=True)
            else:
                print(f"Body: {resp.text[:1000]}", flush=True)
    except Exception as e:
        print(f"Error: {e}", flush=True)


async def test_shop_id_redirect():
    print("\n=== Test 4: shop-id.tokopedia.com redirect chain ===", flush=True)
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False, proxy=PROXY) as c:
            resp = await c.get(seo_url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
            })
            print(f"Status: {resp.status_code}", flush=True)
            print(f"Location: {resp.headers.get('location','')}", flush=True)
            ct = resp.headers.get("content-type", "")
            print(f"CT: {ct}", flush=True)
            print(f"Body: {resp.text[:1500]}", flush=True)
    except Exception as e:
        print(f"Error: {e}", flush=True)


async def test_tiktok_shop_page():
    """Try accessing TikTok Shop product page directly."""
    print("\n=== Test 5: TikTok Shop product page ===", flush=True)
    # TikTok has shop pages at /shop/product/...
    urls = [
        f"https://www.tiktok.com/view/product/{product_id}",
        f"https://shop.tiktok.com/view/product/{product_id}?region=ID",
        f"https://seller-id.tiktok.com/product/{product_id}",
    ]
    async with httpx.AsyncClient(timeout=15, follow_redirects=True, proxy=PROXY) as c:
        for url in urls:
            try:
                resp = await c.get(url, headers={
                    "User-Agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 Chrome/124.0.0.0 Mobile Safari/537.36",
                    "Accept": "text/html",
                    "Accept-Language": "id-ID,id;q=0.9",
                })
                print(f"\n  URL: {url[:80]}", flush=True)
                print(f"  Status: {resp.status_code}", flush=True)
                text = resp.text.lower()
                if "price" in text:
                    prices = re.findall(r"[Rr]p\s?[\d.,]+", resp.text)
                    print(f"  💰 Prices: {prices[:5]}", flush=True)
                elif resp.status_code < 400:
                    print(f"  Body: {resp.text[:300]}", flush=True)
            except Exception as e:
                print(f"  Error: {e}", flush=True)


async def test_playwright_tokopedia():
    """Use Playwright to render Tokopedia product page and extract price."""
    print("\n=== Test 6: Playwright → Tokopedia product page ===", flush=True)
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("Playwright not installed", flush=True)
        return

    proxy_parts = PROXY.replace("http://", "").split("@")
    user_pass = proxy_parts[0].split(":")
    host_port = proxy_parts[1].split(":")
    pw_proxy = {
        "server": f"http://{host_port[0]}:{host_port[1]}",
        "username": user_pass[0],
        "password": user_pass[1],
    }

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = await browser.new_context(
            proxy=pw_proxy,
            user_agent="Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
            locale="id-ID",
            viewport={"width": 412, "height": 915},
        )
        page = await ctx.new_page()

        # Navigate to the seo_url and see where it goes
        print(f"  Navigating to: {seo_url[:80]}...", flush=True)
        try:
            resp = await page.goto(seo_url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(3000)
            final_url = page.url
            print(f"  Final URL: {final_url}", flush=True)
            title = await page.title()
            print(f"  Title: {title}", flush=True)

            # Check for price
            content = await page.content()
            if "Rp" in content:
                prices = re.findall(r"Rp\s?[\d.,]+", content)
                print(f"  💰 Prices in page: {prices[:10]}", flush=True)

            # Try to find price elements
            price_sels = [
                "[class*='price']", "[data-testid*='price']",
                "[class*='Price']", "span:has-text('Rp')",
            ]
            for sel in price_sels:
                try:
                    els = await page.query_selector_all(sel)
                    if els:
                        texts = []
                        for el in els[:5]:
                            t = await el.text_content()
                            if t:
                                texts.append(t.strip())
                        if texts:
                            print(f"  Selector '{sel}': {texts}", flush=True)
                except:
                    pass

            # Check if there's a security check
            if "verify" in content.lower() or "security" in content.lower():
                print("  🔒 Security check detected", flush=True)
                # Take screenshot
                await page.screenshot(path="scripts/tokopedia_page.png")
                print("  Screenshot saved to scripts/tokopedia_page.png", flush=True)

        except Exception as e:
            print(f"  Navigation error: {e}", flush=True)

        await browser.close()


async def test_tiktok_video_page_price():
    """
    Open TikTok video page in Playwright and intercept ALL network requests.
    Look for any request that contains price data.
    """
    print("\n=== Test 7: TikTok video page - intercept ALL requests for price ===", flush=True)
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("Playwright not installed", flush=True)
        return

    proxy_parts = PROXY.replace("http://", "").split("@")
    user_pass = proxy_parts[0].split(":")
    host_port = proxy_parts[1].split(":")
    pw_proxy = {
        "server": f"http://{host_port[0]}:{host_port[1]}",
        "username": user_pass[0],
        "password": user_pass[1],
    }

    video_url = "https://www.tiktok.com/@amosthiosa/video/7622303845783309575"
    price_hits = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            proxy=pw_proxy,
            user_agent="Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
            locale="id-ID",
        )
        page = await ctx.new_page()

        async def on_response(response):
            url = response.url
            ct = response.headers.get("content-type", "")
            if "json" not in ct and "javascript" not in ct:
                return
            try:
                body = await response.text()
                # Search for price patterns: "price":"12345", "price":12345, Rp
                if re.search(r'"price"\s*:\s*"?[1-9]', body) or "Rp " in body or "Rp." in body:
                    # Extract some context
                    matches = re.findall(r'"price"\s*:\s*"?(\d+)"?', body)
                    rp_matches = re.findall(r'Rp\s?[\d.,]+', body)
                    if matches or rp_matches:
                        hit = {
                            "url": url[:120],
                            "prices": matches[:5],
                            "rp": rp_matches[:5],
                        }
                        price_hits.append(hit)
                        print(f"  💰 PRICE HIT: {json.dumps(hit, indent=2)}", flush=True)
            except:
                pass

        page.on("response", on_response)

        print(f"  Opening: {video_url}", flush=True)
        await page.goto(video_url, wait_until="domcontentloaded", timeout=25000)
        await page.wait_for_timeout(5000)

        # Try clicking on product link/anchor if visible
        try:
            anchor = await page.query_selector("[class*='product'], [class*='anchor'], [data-e2e*='product']")
            if anchor:
                print("  Found product anchor, clicking...", flush=True)
                await anchor.click(force=True)
                await page.wait_for_timeout(5000)
        except Exception as e:
            print(f"  Anchor click: {e}", flush=True)

        # Also check page content for price
        content = await page.content()
        rp_in_page = re.findall(r"Rp\s?[\d.,]+", content)
        if rp_in_page:
            print(f"  💰 Rp in page HTML: {rp_in_page[:10]}", flush=True)

        # Check __NEXT_DATA__ or similar
        try:
            script_data = await page.evaluate("""() => {
                const scripts = document.querySelectorAll('script[type="application/json"]');
                let result = [];
                scripts.forEach(s => {
                    const t = s.textContent || '';
                    if (t.includes('price') && t.includes('product')) {
                        result.push(t.substring(0, 500));
                    }
                });
                return result;
            }""")
            if script_data:
                print(f"  Script data with price: {script_data[:3]}", flush=True)
        except:
            pass

        print(f"\n  Total price hits from network: {len(price_hits)}", flush=True)
        await browser.close()


async def main():
    await test_googlebot()
    await test_shop_id_redirect()
    await test_tokopedia_gql()
    await test_tiktok_shop_page()
    await test_playwright_tokopedia()
    await test_tiktok_video_page_price()


if __name__ == "__main__":
    asyncio.run(main())
