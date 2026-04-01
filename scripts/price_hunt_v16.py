"""
Try to get Tokopedia product price via various methods:
1. Tokopedia GQL API (PDPGetLayoutQuery)  
2. Tokopedia mobile web (m.tokopedia.com)
3. Direct product page with proper headers
4. Tokopedia discovery API
5. Google Shopping cache
"""
import asyncio
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

PROXY_URL = "http://5sjQhR7dWXPoSuv:gAbLujfGLSP2rWU@178.93.21.156:49644"

# Product from the live E2E test
PRODUCT_URL = "https://shop-id.tokopedia.com/pdp/pena-uji-kualitas-air-tds-profesional-alat-uji-minuman-presisi-tinggi/1731154567051904037?source=anchor"
PRODUCT_NAME = "Pena uji kualitas air TDS profesional"
PRODUCT_ID = "1731154567051904037"

DESKTOP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

MOBILE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9",
}


async def method_1_gql():
    """Try Tokopedia's GraphQL API directly."""
    print("\n📊 Method 1: Tokopedia GraphQL API")
    
    # Extract slug from URL
    slug_match = re.search(r'/pdp/([^/?#]+)/', PRODUCT_URL)
    slug = slug_match.group(1) if slug_match else ""
    print(f"  Slug: {slug}")
    
    gql_url = "https://gql.tokopedia.com/graphql/PDPGetLayoutQuery"
    
    # Tokopedia GQL query
    payload = [{
        "operationName": "PDPGetLayoutQuery",
        "variables": {
            "shopDomain": "",
            "productKey": slug,
            "layoutID": "",
            "apiVersion": 1,
            "extParam": "",
        },
        "query": """query PDPGetLayoutQuery($shopDomain: String, $productKey: String, $layoutID: String, $apiVersion: Float, $extParam: String) {
          pdpGetLayout(shopDomain: $shopDomain, productKey: $productKey, layoutID: $layoutID, apiVersion: $apiVersion, extParam: $extParam) {
            name
            basicInfo {
              alias
              isQA
              id: productID
              shopID
              shopName
              minOrder
              maxOrder
              weight
              weightUnit
              condition
              status
              url
              needPrescription
              catalogID
              isLeasing
              isBlacklisted
              isTokoNow
              menu {
                id
                name
                url
              }
              category {
                id
                name
                title
                breadcrumbURL
                isAdult
                isKyc
                detail {
                  id
                  name
                  breadcrumbURL
                  isAdult
                }
              }
              txStats {
                transactionSuccess
                transactionReject
                countSold
                paymentVerified
                itemSoldPaymentVerified
              }
              stats {
                countView
                countReview
                countTalk
                rating
              }
            }
            components {
              name
              type
              position
              data {
                ... on pdpDataProductContent {
                  name
                  price {
                    value
                    currency
                    priceFmt
                    slashPriceFmt
                    discPercentage
                  }
                  campaign {
                    campaignID
                    campaignType
                    campaignTypeName
                    campaignIdentifier
                    background
                    percentageAmount
                    originalPrice
                    discountedPrice
                    stock
                    stockSoldPercentage
                    startDate
                    endDate
                    endDateUnix
                    appLinks
                    isAppsOnly
                    isActive
                    hideGimmick
                    isCheckImei
                    minOrder
                    showStockBar
                  }
                  stock {
                    useStock
                    value
                    stockWording
                  }
                }
              }
            }
          }
        }""",
    }]
    
    headers = {
        "User-Agent": DESKTOP_HEADERS["User-Agent"],
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://www.tokopedia.com",
        "Referer": "https://www.tokopedia.com/",
        "X-Tkpd-Akamai": "pdpGetLayout",
        "Accept-Language": "id-ID,id;q=0.9",
    }
    
    try:
        async with httpx.AsyncClient(timeout=15, proxy=PROXY_URL, follow_redirects=True) as client:
            resp = await client.post(gql_url, json=payload, headers=headers)
            print(f"  Status: {resp.status_code}, Size: {len(resp.content)}")
            if resp.status_code == 200 and resp.content:
                data = resp.json()
                print(f"  Response: {json.dumps(data, indent=2)[:1000]}")
            else:
                print(f"  Response text: {resp.text[:500]}")
    except Exception as e:
        print(f"  Error: {e}")


async def method_2_mobile():
    """Try Tokopedia mobile web."""
    print("\n📊 Method 2: Tokopedia Mobile Web")
    
    # Convert to mobile URL
    mobile_url = PRODUCT_URL.replace("shop-id.tokopedia.com", "m.tokopedia.com")
    print(f"  URL: {mobile_url[:100]}")
    
    try:
        async with httpx.AsyncClient(timeout=15, proxy=PROXY_URL, follow_redirects=True) as client:
            resp = await client.get(mobile_url, headers=MOBILE_HEADERS)
            print(f"  Status: {resp.status_code}, Size: {len(resp.content)}")
            body = resp.text[:3000]
            if "security" in body.lower() or "captcha" in body.lower():
                print(f"  ❌ Security check / CAPTCHA")
            elif "Rp" in body:
                # Extract prices
                prices = re.findall(r'Rp[\s.]?[\d.,]+', body)
                print(f"  Found prices: {prices[:5]}")
            else:
                print(f"  Body snippet: {body[:300]}")
    except Exception as e:
        print(f"  Error: {e}")


async def method_3_google_search():
    """Search Google for the product + price."""
    print("\n📊 Method 3: Google Search")
    
    query = f"site:tokopedia.com {PRODUCT_NAME} harga"
    search_url = f"https://www.google.com/search?q={httpx.QueryParams({'q': query})['q']}&hl=id"
    
    headers = {
        "User-Agent": DESKTOP_HEADERS["User-Agent"],
        "Accept": "text/html",
        "Accept-Language": "id-ID,id;q=0.9",
    }
    
    try:
        async with httpx.AsyncClient(timeout=15, proxy=PROXY_URL, follow_redirects=True) as client:
            resp = await client.get(search_url, headers=headers)
            print(f"  Status: {resp.status_code}, Size: {len(resp.content)}")
            body = resp.text
            
            if "captcha" in body.lower() or "unusual traffic" in body.lower():
                print(f"  ❌ Google CAPTCHA")
            else:
                # Look for Rp prices in search results
                prices = re.findall(r'Rp[\s.]?[\d.,]+', body)
                print(f"  Rp prices found: {prices[:10]}")
                
                # Look for price in structured data
                ld_matches = re.findall(r'"price":\s*"?([\d.,]+)"?', body)
                print(f"  JSON price matches: {ld_matches[:5]}")
    except Exception as e:
        print(f"  Error: {e}")


async def method_4_tiktok_shop_api():
    """Try TikTok Shop's product detail API."""
    print("\n📊 Method 4: TikTok Shop Product API")
    
    # Various TikTok Shop API endpoints
    apis = [
        f"https://shop.tiktok.com/api/v1/product/{PRODUCT_ID}",
        f"https://www.tiktok.com/api/ecommerce/product/detail/?product_id={PRODUCT_ID}",
        f"https://oec-api-sg.tiktokv.com/api/v1/oec/product/detail?product_id={PRODUCT_ID}&source=anchor",
        f"https://shop-id.tokopedia.com/api/v1/product/{PRODUCT_ID}",
    ]
    
    headers = {
        "User-Agent": MOBILE_HEADERS["User-Agent"],
        "Accept": "application/json",
        "Accept-Language": "id-ID,id;q=0.9",
    }
    
    async with httpx.AsyncClient(timeout=15, proxy=PROXY_URL, follow_redirects=True) as client:
        for api_url in apis:
            try:
                resp = await client.get(api_url, headers=headers)
                print(f"\n  URL: {api_url[:100]}")
                print(f"  Status: {resp.status_code}, Size: {len(resp.content)}")
                if resp.content:
                    body = resp.text[:500]
                    # Check for price data
                    if "price" in body.lower() or "Rp" in body:
                        print(f"  🔥 Has price data: {body[:300]}")
                    else:
                        print(f"  Body: {body[:200]}")
            except Exception as e:
                print(f"\n  URL: {api_url[:100]}")
                print(f"  Error: {e}")


async def method_5_bing_search():
    """Try Bing search (often less aggressive CAPTCHA)."""
    print("\n📊 Method 5: Bing Search")
    
    query = f"{PRODUCT_NAME} tokopedia harga"
    search_url = f"https://www.bing.com/search?q={httpx.QueryParams({'q': query})['q']}"
    
    headers = {
        "User-Agent": DESKTOP_HEADERS["User-Agent"],
        "Accept": "text/html",
        "Accept-Language": "id-ID,id;q=0.9",
    }
    
    try:
        async with httpx.AsyncClient(timeout=15, proxy=PROXY_URL, follow_redirects=True) as client:
            resp = await client.get(search_url, headers=headers)
            print(f"  Status: {resp.status_code}, Size: {len(resp.content)}")
            body = resp.text
            
            # Look for Rp prices
            prices = re.findall(r'Rp[\s.]?[\d.,]+', body)
            print(f"  Rp prices found: {prices[:10]}")
            
            # Look for structured price data
            ld_matches = re.findall(r'"price"[:\s]+"?([\d.,]+)"?', body)
            print(f"  JSON price matches: {ld_matches[:5]}")
            
            if not prices and not ld_matches:
                # Check what we got
                if "captcha" in body.lower():
                    print("  ❌ Bing CAPTCHA")
                else:
                    print(f"  Body snippet: {body[:300]}")
    except Exception as e:
        print(f"  Error: {e}")


async def method_6_duckduckgo():
    """Try DuckDuckGo (no CAPTCHA usually)."""
    print("\n📊 Method 6: DuckDuckGo Search")
    
    query = f"{PRODUCT_NAME} tokopedia harga Rp"
    search_url = f"https://html.duckduckgo.com/html/?q={httpx.QueryParams({'q': query})['q']}"
    
    headers = {
        "User-Agent": DESKTOP_HEADERS["User-Agent"],
        "Accept": "text/html",
        "Accept-Language": "id-ID,id;q=0.9",
    }
    
    try:
        async with httpx.AsyncClient(timeout=15, proxy=PROXY_URL, follow_redirects=True) as client:
            resp = await client.get(search_url, headers=headers)
            print(f"  Status: {resp.status_code}, Size: {len(resp.content)}")
            body = resp.text
            
            # Look for Rp prices
            prices = re.findall(r'Rp[\s.]?[\d.,]+', body)
            print(f"  Rp prices found: {prices[:10]}")
            
            # Extract search result snippets that mention price
            snippets = re.findall(r'class="result__snippet">(.*?)</a>', body, re.DOTALL)
            for s in snippets[:5]:
                clean = re.sub(r'<[^>]+>', '', s).strip()
                if any(kw in clean.lower() for kw in ['rp', 'harga', 'price', 'ribu']):
                    print(f"  Snippet: {clean[:150]}")
    except Exception as e:
        print(f"  Error: {e}")


async def method_7_tokopedia_sitemaps():
    """Try to get price from Tokopedia's open data sources."""
    print("\n📊 Method 7: Tokopedia Discovery/Affiliate API")
    
    # Tokopedia has an affiliate program with public product search
    affiliate_url = f"https://affiliate.tokopedia.com/api/product/search?q={PRODUCT_NAME[:50]}"
    
    headers = {
        "User-Agent": DESKTOP_HEADERS["User-Agent"],
        "Accept": "application/json",
    }
    
    try:
        async with httpx.AsyncClient(timeout=15, proxy=PROXY_URL, follow_redirects=True) as client:
            resp = await client.get(affiliate_url, headers=headers)
            print(f"  Affiliate API: status={resp.status_code}, size={len(resp.content)}")
            if resp.content:
                print(f"  Body: {resp.text[:300]}")
    except Exception as e:
        print(f"  Affiliate API error: {e}")


async def main():
    print(f"Product: {PRODUCT_NAME}")
    print(f"URL: {PRODUCT_URL[:100]}")
    print(f"ID: {PRODUCT_ID}")
    
    await method_1_gql()
    await method_2_mobile()
    await method_3_google_search()
    await method_4_tiktok_shop_api()
    await method_5_bing_search()
    await method_6_duckduckgo()
    await method_7_tokopedia_sitemaps()


if __name__ == "__main__":
    asyncio.run(main())
