#!/usr/bin/env python3
"""
Try to get price from Tokopedia by:
1. Resolving shop-id.tokopedia.com to actual Tokopedia product URL
2. Using Tokopedia's mobile GraphQL API
3. Using Google cache of the product page
4. Using web.archive.org cached version
"""
import asyncio, json, re, os, urllib.parse
from pathlib import Path
from dotenv import load_dotenv
import httpx

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
PROXY = os.getenv("PROXY_URL", "")

# Product info from our scraping
PRODUCT_ID = "1732773678384055322"
PRODUCT_TITLE = "Pembersih Busa Sofa Kain 500ml Antibakteri Formula Lembut Wangi Tahan 24 Jam Penghilang Noda Kuat"
SEO_URL = f"https://shop-id.tokopedia.com/pdp/pembersih-busa-sofa-kain-500ml-antibakteri-formula-lembut-wangi-tahan-24-jam-penghilang-noda-kuat/{PRODUCT_ID}"


async def test_google_search():
    """Search Google for the product to find Tokopedia listing with price."""
    print("\n=== Test 1: Google search for product ===", flush=True)
    query = urllib.parse.quote(f"site:tokopedia.com {PRODUCT_TITLE[:60]}")
    url = f"https://www.google.com/search?q={query}&hl=id&gl=id"
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True, proxy=PROXY) as c:
            resp = await c.get(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
                "Accept": "text/html",
                "Accept-Language": "id-ID,id;q=0.9",
            })
            print(f"  Status: {resp.status_code}", flush=True)
            # Extract Tokopedia URLs from results
            tkpd_urls = re.findall(r'https://www\.tokopedia\.com/[^"&\s<>]+', resp.text)
            if tkpd_urls:
                print(f"  Tokopedia URLs found: {len(tkpd_urls)}", flush=True)
                for u in tkpd_urls[:5]:
                    print(f"    {u}", flush=True)
            # Extract prices from Google snippet
            prices = re.findall(r'Rp\s?[\d.,]+', resp.text)
            if prices:
                print(f"  💰 Prices in Google results: {prices[:10]}", flush=True)
            else:
                print(f"  No prices found in search results", flush=True)
                # Check if blocked
                if "captcha" in resp.text.lower() or "unusual traffic" in resp.text.lower():
                    print("  🔒 Google blocked request", flush=True)
    except Exception as e:
        print(f"  Error: {e}", flush=True)


async def test_tokopedia_search_api():
    """Use Tokopedia's search/autocomplete API to find product with price."""
    print("\n=== Test 2: Tokopedia search/autocomplete API ===", flush=True)
    
    # Tokopedia has a public search API
    search_term = "pembersih busa sofa kain 500ml"
    
    endpoints = [
        # Search API
        (f"https://gql.tokopedia.com/graphql/SearchProductQueryV4", "POST", {
            "operationName": "SearchProductQueryV4",
            "variables": json.dumps({"params": f"q={search_term}&page=1&rows=5&source=search"}),
            "query": "query SearchProductQueryV4($params:String!){ace_search_product_v4(params:$params){data{products{id name price{text}imageUrl shop{name}url}}}}"
        }),
        # Universe autocomplete
        (f"https://gql.tokopedia.com/", "POST", [{
            "operationName": "universe_placeholder",
            "variables": {"query": search_term, "source": "search"},
            "query": "query universe_placeholder($query:String!,$source:String!){universe_placeholder(query:$query,source:$source){data{id title url imageURI price}}}"
        }]),
    ]
    
    for url, method, body in endpoints:
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 Chrome/131.0.0.0 Mobile Safari/537.36",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Origin": "https://www.tokopedia.com",
                "Referer": "https://www.tokopedia.com/",
                "X-Source": "tokopedia-lite",
                "X-Tkpd-Akamai": "pdpGetLayout",
            }
            async with httpx.AsyncClient(timeout=15, proxy=PROXY) as c:
                if method == "POST":
                    resp = await c.post(url, json=body, headers=headers)
                else:
                    resp = await c.get(url, headers=headers)
                
                print(f"  [{method}] {url[:80]}", flush=True)
                print(f"  Status: {resp.status_code}, CT: {resp.headers.get('content-type','')}", flush=True)
                
                if resp.status_code == 200:
                    text = resp.text[:3000]
                    if "price" in text.lower():
                        print(f"  💰 Has price data!", flush=True)
                    print(f"  Body: {text[:1000]}", flush=True)
                else:
                    print(f"  Body: {resp.text[:500]}", flush=True)
        except Exception as e:
            print(f"  Error: {e}", flush=True)


async def test_tokopedia_discovery():
    """Try Tokopedia discovery/recommendation API."""
    print("\n=== Test 3: Tokopedia discovery API ===", flush=True)
    
    urls = [
        f"https://www.tokopedia.com/ajax/p/8/recommendation/product?productId={PRODUCT_ID}",
        f"https://gql.tokopedia.com/graphql/PDPGetLayoutQuery",
    ]
    
    for url in urls:
        try:
            async with httpx.AsyncClient(timeout=15, proxy=PROXY, follow_redirects=True) as c:
                if "graphql" in url:
                    body = [{
                        "operationName": "PDPGetLayoutQuery",
                        "variables": {
                            "shopDomain": "",
                            "productKey": "",
                            "layoutID": "",
                            "apiVersion": 1,
                            "extParam": f"productID={PRODUCT_ID}",
                        },
                        "query": "query PDPGetLayoutQuery($shopDomain:String,$productKey:String,$layoutID:String,$apiVersion:Float,$extParam:String){pdpGetLayout(shopDomain:$shopDomain,productKey:$productKey,layoutID:$layoutID,apiVersion:$apiVersion,extParam:$extParam){requestID name basicInfo{id shopID shopName price}}}"
                    }]
                    resp = await c.post(url, json=body, headers={
                        "Content-Type": "application/json",
                        "Origin": "https://www.tokopedia.com",
                        "Referer": "https://www.tokopedia.com/",
                        "X-Source": "tokopedia-lite",
                    })
                else:
                    resp = await c.get(url, headers={
                        "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36",
                        "Accept": "application/json",
                    })
                
                print(f"  URL: {url[:80]}", flush=True)
                print(f"  Status: {resp.status_code}", flush=True)
                if resp.status_code == 200:
                    text = resp.text[:2000]
                    if "price" in text.lower():
                        print(f"  💰 Has price!", flush=True)
                    print(f"  Body: {text[:1000]}", flush=True)
        except Exception as e:
            print(f"  Error: {e}", flush=True)


async def test_google_cache():
    """Try Google cache of Tokopedia product page."""
    print("\n=== Test 4: Google cache / web archive ===", flush=True)
    
    # Try Wayback Machine
    url = f"https://web.archive.org/web/2025/https://www.tokopedia.com/*{PRODUCT_ID}*"
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:  # No proxy for archive.org
            resp = await c.get(url, headers={"User-Agent": "Mozilla/5.0"})
            print(f"  Wayback: status={resp.status_code}", flush=True)
            if "tokopedia.com" in resp.text:
                urls_found = re.findall(r'https://www\.tokopedia\.com/[^\s"<>]+', resp.text)
                print(f"  URLs: {urls_found[:3]}", flush=True)
    except Exception as e:
        print(f"  Error: {e}", flush=True)


async def test_tiktok_app_api():
    """
    Try TikTok's mobile app API to get product details with price.
    The mobile app uses different endpoints than the web.
    """
    print("\n=== Test 5: TikTok mobile app API endpoints ===", flush=True)
    
    # Common TikTok mobile API base URLs
    endpoints = [
        # Commerce product detail
        f"https://api16-normal-useast5.us.tiktokv.com/aweme/v1/commerce/product/detail/?product_id={PRODUCT_ID}&aid=1233&region=ID",
        f"https://api16-normal-c-useast1a.tiktokv.com/aweme/v1/commerce/product/detail/?product_id={PRODUCT_ID}&aid=1233&region=ID",
        # OEC API v2
        f"https://oec-api-sg.tiktokv.com/api/v2/product/detail?product_id={PRODUCT_ID}&region=ID",
        # TikTok Shop API 
        f"https://api16-normal-useast5.us.tiktokv.com/aweme/v1/shop/product/detail/?product_id={PRODUCT_ID}&aid=1233",
        # Ecom API
        f"https://ecom-api-sg.tiktokv.com/api/v1/product/detail?product_id={PRODUCT_ID}&region=ID",
    ]
    
    for url in endpoints:
        try:
            async with httpx.AsyncClient(timeout=10, proxy=PROXY, follow_redirects=True) as c:
                resp = await c.get(url, headers={
                    "User-Agent": "com.zhiliaoapp.musically/2023501030 (Linux; U; Android 14; en_US; SM-S928B; Build/UP1A.231005.007; Cronet/TTNetVersion:0c06b3f6 2023-11-28 QuicVersion:9b5f6470 2023-10-23)",
                    "Accept": "application/json",
                    "X-Tt-Token": "",
                })
                print(f"\n  URL: {url[:100]}", flush=True)
                print(f"  Status: {resp.status_code}", flush=True)
                if resp.status_code == 200:
                    text = resp.text[:2000]
                    if "price" in text.lower() or "Rp" in text:
                        print(f"  💰 Has price data!", flush=True)
                    print(f"  Body: {text[:500]}", flush=True)
        except Exception as e:
            print(f"  Error: {e.__class__.__name__}: {str(e)[:80]}", flush=True)


async def test_tiktok_share_page():
    """
    TikTok share/SEO pages sometimes render product info for bots.
    Try the /oembed endpoint and share page.
    """
    print("\n=== Test 6: TikTok oembed / share endpoints ===", flush=True)
    
    video_url = "https://www.tiktok.com/@amosthiosa/video/7622303845783309575"
    
    endpoints = [
        # oEmbed (public, no auth needed)
        f"https://www.tiktok.com/oembed?url={urllib.parse.quote(video_url)}",
        # TikTok API for video detail
        f"https://www.tiktok.com/api/item/detail/?itemId=7622303845783309575&aid=1988",
    ]
    
    for url in endpoints:
        try:
            async with httpx.AsyncClient(timeout=15, proxy=PROXY, follow_redirects=True) as c:
                resp = await c.get(url, headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept": "application/json",
                })
                print(f"\n  URL: {url[:100]}", flush=True)
                print(f"  Status: {resp.status_code}", flush=True)
                if resp.status_code == 200:
                    text = resp.text
                    if "price" in text.lower():
                        print(f"  💰 Has price!", flush=True)
                    if "anchor" in text.lower():
                        print(f"  🔗 Has anchor data!", flush=True)
                    # Save full response
                    if len(text) > 500:
                        # Check for product/anchor data
                        try:
                            data = json.loads(text)
                            # Walk the JSON for anchor/price data
                            ds = json.dumps(data)
                            if "anchors" in ds:
                                print(f"  🔗 Contains 'anchors' in JSON!", flush=True)
                            if "product" in ds.lower():
                                print(f"  📦 Contains 'product' in JSON!", flush=True)
                        except:
                            pass
                    print(f"  Body: {text[:500]}", flush=True)
        except Exception as e:
            print(f"  Error: {e.__class__.__name__}: {str(e)[:80]}", flush=True)


async def main():
    await test_tiktok_share_page()
    await test_tiktok_app_api()
    await test_tokopedia_search_api()
    await test_google_search()
    await test_tokopedia_discovery()
    print("\n✅ All tests done", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
