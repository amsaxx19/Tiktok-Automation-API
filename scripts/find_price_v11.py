#!/usr/bin/env python3
"""
Try the OEC ecommerce view URL found in product data.
Also try the Tokopedia AJAX product API with proper cookies.
"""
import asyncio, json, re, os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
PROXY = os.getenv("PROXY_URL", "")

# From product data
DETAIL_URL = "https://oec-api.tiktokv.com/view/fe_tiktok_ecommerce_upgrade/index.html?enter_from=video&hide_nav_bar=1&view_background_color_auto_dark=1&should_full_screen=1"
PRODUCT_ID = "1732773678384055322"
SELLER_ID = "7494180771761980442"


async def try_oec_view():
    """Load the OEC ecommerce view in browser."""
    print("=== OEC Ecommerce View ===", flush=True)
    from playwright.async_api import async_playwright

    proxy_parts = PROXY.replace("http://", "").split("@")
    user_pass = proxy_parts[0].split(":")
    host_port = proxy_parts[1].split(":")
    pw_proxy = {
        "server": f"http://{host_port[0]}:{host_port[1]}",
        "username": user_pass[0],
        "password": user_pass[1],
    }

    json_responses = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            proxy=pw_proxy,
            user_agent="Mozilla/5.0 (Linux; Android 14; SM-S928B; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/131.0.0.0 Mobile Safari/537.36 BytedanceWebview/d8a21c6",
            locale="id-ID",
            viewport={"width": 412, "height": 915},
            is_mobile=True,
        )
        page = await ctx.new_page()

        async def on_resp(response):
            ct = response.headers.get("content-type", "")
            if response.status == 200 and "json" in ct:
                try:
                    body = await response.text()
                    if len(body) > 100:
                        json_responses.append({"url": response.url[:150], "size": len(body), "body": body})
                        if re.search(r'"price"\s*:\s*"?[1-9]', body):
                            print(f"  💰 JSON with price: {response.url[:100]}", flush=True)
                except:
                    pass

        page.on("response", on_resp)

        # Build URL with product params
        params = f"&product_id={PRODUCT_ID}&seller_id={SELLER_ID}&region=ID"
        full_url = DETAIL_URL + params
        print(f"  URL: {full_url[:120]}...", flush=True)
        
        try:
            await page.goto(full_url, wait_until="domcontentloaded", timeout=20000)
        except Exception as e:
            print(f"  Nav: {e}", flush=True)
        
        await page.wait_for_timeout(5000)
        print(f"  Title: {await page.title()}", flush=True)
        
        content = await page.content()
        rp = re.findall(r"Rp\s?[\d.,]+", content)
        if rp:
            print(f"  💰 Rp in page: {rp[:10]}", flush=True)
        
        print(f"  JSON responses: {len(json_responses)}", flush=True)
        for jr in json_responses[:5]:
            print(f"    {jr['url'][:80]} ({jr['size']}B)", flush=True)
        
        # Also try the product page URL format
        pdp_url = f"https://oec-api.tiktokv.com/view/product/{PRODUCT_ID}?region=ID"
        print(f"\n  Trying: {pdp_url}", flush=True)
        try:
            await page.goto(pdp_url, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(3000)
            print(f"  Title: {await page.title()}", flush=True)
            content = await page.content()
            rp = re.findall(r"Rp\s?[\d.,]+", content)
            if rp:
                print(f"  💰 Rp: {rp[:10]}", flush=True)
            else:
                print(f"  Body: {content[:300]}", flush=True)
        except Exception as e:
            print(f"  Error: {e}", flush=True)

        await browser.close()


async def try_tokopedia_with_cookies():
    """
    Navigate to Tokopedia homepage first to get cookies/session,
    then try to access the product page or GraphQL API.
    """
    print("\n\n=== Tokopedia with session cookies ===", flush=True)
    from playwright.async_api import async_playwright

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
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="id-ID",
            viewport={"width": 1366, "height": 768},
        )
        page = await ctx.new_page()

        # Step 1: Visit Tokopedia homepage
        print("  Step 1: Visit tokopedia.com...", flush=True)
        try:
            await page.goto("https://www.tokopedia.com/", wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(5000)
            title = await page.title()
            print(f"  Homepage: {title}", flush=True)
            
            cookies = await ctx.cookies()
            print(f"  Cookies: {len(cookies)}", flush=True)
            
            # Check if homepage loaded properly
            if "tokopedia" in title.lower() or "jual" in (await page.content()).lower():
                print("  ✅ Homepage loaded", flush=True)
                
                # Step 2: Try search for product
                print("\n  Step 2: Search for product...", flush=True)
                search_url = "https://www.tokopedia.com/search?q=seumnida+sofa+stain+remover"
                await page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_timeout(5000)
                title = await page.title()
                print(f"  Search title: {title}", flush=True)
                
                content = await page.content()
                if "security" in title.lower() or "verify" in content.lower():
                    print("  🔒 Security check on search", flush=True)
                else:
                    rp = re.findall(r"Rp\s?[\d.,]+", content)
                    print(f"  💰 Prices: {rp[:10]}", flush=True)
                    
                    # Try GraphQL with cookies
                    print("\n  Step 3: Try GraphQL with session...", flush=True)
                    gql_result = await page.evaluate("""async () => {
                        try {
                            const resp = await fetch('https://gql.tokopedia.com/graphql/SearchProductQueryV4', {
                                method: 'POST',
                                credentials: 'include',
                                headers: {
                                    'Content-Type': 'application/json',
                                    'Accept': 'application/json',
                                    'X-Source': 'tokopedia-lite',
                                },
                                body: JSON.stringify([{
                                    operationName: 'SearchProductQueryV4',
                                    variables: {
                                        params: 'q=seumnida sofa stain remover&page=1&rows=5&source=search'
                                    },
                                    query: 'query SearchProductQueryV4($params:String!){ace_search_product_v4(params:$params){data{products{id name price{text textIdr}imageUrl shop{name city}url}}}}'
                                }])
                            });
                            const text = await resp.text();
                            return {status: resp.status, body: text.substring(0, 5000)};
                        } catch(e) {
                            return {error: e.message};
                        }
                    }""")
                    
                    body = gql_result.get("body", "")
                    if "price" in body.lower():
                        print(f"  💰 GraphQL has price data!", flush=True)
                    print(f"  GQL result: {json.dumps(gql_result, indent=2)[:2000]}", flush=True)
            else:
                print(f"  ❌ Homepage might be blocked: {title}", flush=True)
                
        except Exception as e:
            print(f"  Error: {e}", flush=True)

        await browser.close()


async def main():
    await try_oec_view()
    await try_tokopedia_with_cookies()
    print("\n✅ All done", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
