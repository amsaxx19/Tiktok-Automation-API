"""
DuckDuckGo price extraction — deeper analysis.
DDG returned Rp4 snippets. Let's get the full snippets and extract prices.
"""
import asyncio
import json
import re
import sys
import os
import html as html_lib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import httpx

PROXY_URL = "http://5sjQhR7dWXPoSuv:gAbLujfGLSP2rWU@178.93.21.156:49644"

# Test products from our E2E scrape
PRODUCTS = [
    {
        "name": "Pena uji kualitas air TDS profesional",
        "id": "1731154567051904037",
    },
    {
        "name": "[BlanjaBuku] Paket Hemat Isi 3 Buku Self Improvement The Art of Manipulation",
        "id": "1731072564468352712",
    },
    {
        "name": "Seumnida Sofa Instan Stain Remover Spray",
        "id": "1732773678384055322",
    },
    {
        "name": "SJJ Krim penghilang bulu pemutih untuk pria dan wanita",
        "id": "1730591352271439080",
    },
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html",
    "Accept-Language": "id-ID,id;q=0.9",
}


async def search_ddg(product_name: str) -> dict:
    """Search DuckDuckGo and extract price from snippets."""
    query = f"{product_name} tokopedia harga"
    url = f"https://html.duckduckgo.com/html/?q={httpx.QueryParams({'q': query})['q']}"
    
    try:
        async with httpx.AsyncClient(timeout=15, proxy=PROXY_URL, follow_redirects=True) as client:
            resp = await client.get(url, headers=HEADERS)
            body = resp.text
            
            # Extract result snippets
            snippets = re.findall(r'class="result__snippet">(.*?)</a>', body, re.DOTALL)
            
            # Extract result titles  
            titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', body, re.DOTALL)
            
            # Extract result URLs
            urls = re.findall(r'class="result__url"[^>]*>(.*?)</a>', body, re.DOTALL)
            
            # Find Rp prices in the full body
            rp_prices = re.findall(r'Rp[\s.]?[\d.,]+', body)
            
            # Clean and extract price from snippets
            prices_found = []
            for snippet in snippets[:10]:
                clean = html_lib.unescape(re.sub(r'<[^>]+>', '', snippet).strip())
                # Look for Rp pattern in snippet
                rp_match = re.findall(r'Rp[\s.]?[\d.,]+', clean)
                if rp_match:
                    prices_found.extend(rp_match)
            
            # Also look in titles
            for title in titles[:10]:
                clean = html_lib.unescape(re.sub(r'<[^>]+>', '', title).strip())
                rp_match = re.findall(r'Rp[\s.]?[\d.,]+', clean)
                if rp_match:
                    prices_found.extend(rp_match)
            
            return {
                "status": resp.status_code,
                "body_size": len(body),
                "snippets": len(snippets),
                "rp_in_body": rp_prices[:10],
                "rp_in_snippets": prices_found[:10],
                "first_snippets": [
                    html_lib.unescape(re.sub(r'<[^>]+>', '', s).strip())[:150]
                    for s in snippets[:5]
                ],
                "first_titles": [
                    html_lib.unescape(re.sub(r'<[^>]+>', '', t).strip())[:100]
                    for t in titles[:5]
                ],
            }
    except Exception as e:
        return {"error": str(e)}


async def search_ddg_lite(product_name: str) -> dict:
    """Try DuckDuckGo lite (even simpler HTML)."""
    query = f"{product_name} harga Rp tokopedia"
    url = f"https://lite.duckduckgo.com/lite/?q={httpx.QueryParams({'q': query})['q']}"
    
    try:
        async with httpx.AsyncClient(timeout=15, proxy=PROXY_URL, follow_redirects=True) as client:
            resp = await client.get(url, headers=HEADERS)
            body = resp.text
            
            rp_prices = re.findall(r'Rp[\s.]?[\d.,]+', body)
            
            # Extract table rows (lite DDG uses tables)
            snippets = re.findall(r'class="result-snippet">(.*?)</td>', body, re.DOTALL)
            
            return {
                "status": resp.status_code,
                "body_size": len(body),
                "rp_in_body": rp_prices[:10],
                "snippets_count": len(snippets),
                "first_snippets": [
                    html_lib.unescape(re.sub(r'<[^>]+>', '', s).strip())[:150]
                    for s in snippets[:5]
                ],
            }
    except Exception as e:
        return {"error": str(e)}


async def main():
    print("🦆 DuckDuckGo Price Extraction")
    print("=" * 60)
    
    for product in PRODUCTS:
        name = product["name"]
        pid = product["id"]
        
        print(f"\n📦 Product: {name[:60]}")
        print(f"   ID: {pid}")
        
        # Try HTML version
        result = await search_ddg(name)
        print(f"\n   DDG HTML:")
        print(f"     Status: {result.get('status')}, Size: {result.get('body_size')}")
        print(f"     Snippets: {result.get('snippets')}")
        print(f"     Rp in body: {result.get('rp_in_body')}")
        print(f"     Rp in snippets: {result.get('rp_in_snippets')}")
        if result.get('first_titles'):
            for t in result['first_titles'][:3]:
                print(f"     Title: {t}")
        if result.get('first_snippets'):
            for s in result['first_snippets'][:3]:
                print(f"     Snippet: {s}")
        
        # Try Lite version
        result_lite = await search_ddg_lite(name)
        print(f"\n   DDG Lite:")
        print(f"     Status: {result_lite.get('status')}, Size: {result_lite.get('body_size')}")
        print(f"     Rp in body: {result_lite.get('rp_in_body')}")
        if result_lite.get('first_snippets'):
            for s in result_lite['first_snippets'][:3]:
                print(f"     Snippet: {s}")
        
        print(f"\n{'─'*60}")
        await asyncio.sleep(2)  # Rate limit


if __name__ == "__main__":
    asyncio.run(main())
