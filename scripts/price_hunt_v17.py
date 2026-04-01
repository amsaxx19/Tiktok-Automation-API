"""
Try Tokopedia GQL API directly (no proxy) and with various approaches.
The product URLs are shop-id.tokopedia.com which is TikTok's Tokopedia integration.
We need to resolve the actual Tokopedia shop/product path first.
"""
import asyncio
import json
import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import httpx

# Products from our live scrape
PRODUCTS = [
    {
        "name": "Pena uji kualitas air TDS profesional",
        "url": "https://shop-id.tokopedia.com/pdp/pena-uji-kualitas-air-tds-profesional-alat-uji-minuman-presisi-tinggi/1731154567051904037?source=anchor",
        "id": "1731154567051904037",
    },
    {
        "name": "Seumnida Sofa Instan Stain Remover Spray",
        "url": "https://shop-id.tokopedia.com/pdp/pembersih-busa-sofa-kain-500ml-antibakteri-formula-lembut-wangi-tahan-24-jam-penghilang-noda-kuat/1732773678384055322?source=anchor",
        "id": "1732773678384055322",
    },
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en;q=0.5",
}


async def approach_1_resolve_redirect(product):
    """shop-id.tokopedia.com likely redirects to the real Tokopedia URL. Follow it."""
    print(f"\n📍 Approach 1: Resolve redirect for {product['url'][:80]}")
    
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            resp = await client.get(product['url'], headers=HEADERS)
            print(f"  Status: {resp.status_code}")
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get('location', '')
                print(f"  Redirect to: {location}")
                return location
            elif resp.status_code == 200:
                # Check if it's a JS redirect
                body = resp.text[:3000]
                meta_refresh = re.search(r'url=([^"\'>\s]+)', body, re.IGNORECASE)
                if meta_refresh:
                    print(f"  Meta redirect: {meta_refresh.group(1)}")
                    return meta_refresh.group(1)
                
                # Check for Rp in body
                prices = re.findall(r'Rp[\s.]?[\d.,]+', body)
                if prices:
                    print(f"  Prices found: {prices[:5]}")
                
                # Check title
                title_match = re.search(r'<title>(.*?)</title>', body, re.IGNORECASE)
                if title_match:
                    print(f"  Title: {title_match.group(1)[:100]}")
                
                if "security" in body.lower():
                    print("  ❌ Security check")
                
                print(f"  Body snippet: {body[:200]}")
    except Exception as e:
        print(f"  Error: {e}")
    return None


async def approach_2_tokopedia_gql_no_proxy(product):
    """Try Tokopedia GQL without proxy."""
    print(f"\n📍 Approach 2: Tokopedia GQL (no proxy)")
    
    slug = re.search(r'/pdp/([^/?#]+)/', product['url'])
    slug_text = slug.group(1) if slug else product['name'].lower().replace(' ', '-')
    
    gql_url = "https://gql.tokopedia.com/graphql/PDPGetLayoutQuery"
    
    payload = [{
        "operationName": "PDPGetLayoutQuery",
        "variables": {
            "shopDomain": "",
            "productKey": slug_text,
            "layoutID": "",
            "apiVersion": 1,
        },
        "query": """query PDPGetLayoutQuery($shopDomain: String, $productKey: String, $layoutID: String, $apiVersion: Float) {
          pdpGetLayout(shopDomain: $shopDomain, productKey: $productKey, layoutID: $layoutID, apiVersion: $apiVersion) {
            name
            basicInfo { alias id: productID shopID shopName url txStats { countSold } stats { rating countReview } }
            components { name type data { ... on pdpDataProductContent { name price { value currency priceFmt slashPriceFmt discPercentage } stock { value } } } }
          }
        }""",
    }]
    
    headers = {
        "User-Agent": HEADERS["User-Agent"],
        "Accept": "*/*",
        "Content-Type": "application/json",
        "Origin": "https://www.tokopedia.com",
        "Referer": "https://www.tokopedia.com/",
        "X-Tkpd-Akamai": "pdpGetLayout",
        "X-Source": "tokopedia-lite",
    }
    
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.post(gql_url, json=payload, headers=headers)
            print(f"  Status: {resp.status_code}, Size: {len(resp.content)}")
            if resp.content:
                try:
                    data = resp.json()
                    print(f"  Response: {json.dumps(data, indent=2)[:1000]}")
                except:
                    print(f"  Body: {resp.text[:500]}")
    except Exception as e:
        print(f"  Error: {e}")


async def approach_3_playwright_no_proxy(product):
    """Try Playwright to Tokopedia without proxy (from local IP)."""
    print(f"\n📍 Approach 3: Playwright to Tokopedia (no proxy)")
    
    from playwright.async_api import async_playwright
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=['--disable-blink-features=AutomationControlled'],
        )
        context = await browser.new_context(
            locale='id-ID',
            viewport={'width': 1366, 'height': 768},
            user_agent=HEADERS["User-Agent"],
            extra_http_headers={'Accept-Language': 'id-ID,id;q=0.9,en;q=0.5'},
        )
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)
        
        page = await context.new_page()
        
        try:
            await page.goto(product['url'], wait_until='domcontentloaded', timeout=20000)
            await page.wait_for_timeout(3000)
            
            title = await page.title()
            print(f"  Title: {title[:100]}")
            
            current_url = page.url
            print(f"  Current URL: {current_url[:120]}")
            
            if "security" in title.lower():
                print("  ❌ Security check")
            else:
                # Try to extract price
                data = await page.evaluate("""() => {
                    const result = {};
                    
                    // JSON-LD
                    document.querySelectorAll('script[type="application/ld+json"]').forEach(s => {
                        try {
                            const d = JSON.parse(s.textContent);
                            if (d['@type'] === 'Product' || d.offers) result.jsonld = d;
                        } catch(e) {}
                    });
                    
                    // Price elements
                    const priceEl = document.querySelector('[data-testid="lblPDPDetailProductPrice"]')
                        || document.querySelector('[class*="price"]');
                    if (priceEl) result.price = priceEl.textContent;
                    
                    // Shop name
                    const shopEl = document.querySelector('[data-testid="llbPDPFooterShopName"]')
                        || document.querySelector('a[href*="/shop/"]');
                    if (shopEl) result.shop = shopEl.textContent?.trim();
                    
                    // All Rp prices on page
                    const bodyText = document.body.innerText || '';
                    const rpMatches = bodyText.match(/Rp[\\s.]?[\\d.,]+/g) || [];
                    result.rp_prices = rpMatches.slice(0, 10);
                    
                    // Page text sample
                    result.body_sample = bodyText.substring(0, 500);
                    
                    return result;
                }""")
                
                print(f"  Price: {data.get('price')}")
                print(f"  Shop: {data.get('shop')}")
                print(f"  Rp prices: {data.get('rp_prices')}")
                if data.get('jsonld'):
                    offers = data['jsonld'].get('offers', {})
                    print(f"  JSON-LD price: {offers.get('price') or offers.get('lowPrice')}")
                print(f"  Body sample: {data.get('body_sample', '')[:200]}")
        
        except Exception as e:
            print(f"  Error: {e}")
        
        await browser.close()


async def approach_4_search_tokopedia(product):
    """Search Tokopedia's search API for the product."""
    print(f"\n📍 Approach 4: Tokopedia Search API")
    
    # Tokopedia has a search endpoint
    search_url = f"https://www.tokopedia.com/search?q={httpx.QueryParams({'q': product['name'][:50]})['q']}"
    
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(search_url, headers=HEADERS)
            print(f"  Status: {resp.status_code}, Size: {len(resp.content)}")
            body = resp.text
            
            if "security" in body.lower()[:500]:
                print("  ❌ Security check")
            else:
                prices = re.findall(r'Rp[\s.]?[\d.,]+', body)
                print(f"  Rp prices in page: {prices[:10]}")
                
                # Check for initial search data in script tags
                for match in re.finditer(r'"price":\s*(\d+)', body):
                    print(f"  JSON price: {match.group(1)}")
                    if int(match.group(1)) > 1000:
                        break
    except Exception as e:
        print(f"  Error: {e}")


async def main():
    for product in PRODUCTS[:1]:  # Just test first product
        print(f"\n{'='*60}")
        print(f"📦 {product['name'][:60]}")
        print(f"   URL: {product['url'][:100]}")
        print(f"{'='*60}")
        
        real_url = await approach_1_resolve_redirect(product)
        await approach_2_tokopedia_gql_no_proxy(product)
        await approach_3_playwright_no_proxy(product)
        await approach_4_search_tokopedia(product)


if __name__ == "__main__":
    asyncio.run(main())
