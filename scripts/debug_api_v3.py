"""
Debug: Try TikTok's webapp API endpoints directly to get video detail with anchors.
Uses desktop Playwright to first get cookies/tokens, then hits APIs.
"""
import asyncio
import json
import os
import sys
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

VIDEO_URL = "https://www.tiktok.com/@amosthiosa/video/7507723498614331653"
VIDEO_ID = "7507723498614331653"

PROXY = {
    "server": "http://178.93.21.156:49644",
    "username": "5sjQhR7dWXPoSuv",
    "password": "gAbLujfGLSP2rWU",
}

DESKTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"


async def main():
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            proxy=PROXY,
            locale='id-ID',
            viewport={'width': 1920, 'height': 1080},
            user_agent=DESKTOP_UA,
            extra_http_headers={'Accept-Language': 'id-ID,id;q=0.9,en;q=0.5'},
        )
        page = await context.new_page()

        # Track all responses
        api_responses = {}
        async def on_response(resp):
            url = resp.url
            if "/api/" in url and resp.status == 200:
                try:
                    body = await resp.body()
                    api_responses[url[:200]] = {"size": len(body), "body": body}
                except:
                    pass

        page.on("response", on_response)

        print("📄 Loading video page (desktop)...")
        try:
            await page.goto(VIDEO_URL, wait_until='networkidle', timeout=30000)
        except Exception as e:
            print(f"  Nav: {e}")
        await page.wait_for_timeout(3000)

        # Get cookies
        cookies = await context.cookies()
        print(f"  Cookies: {len(cookies)}")
        cookie_names = [c['name'] for c in cookies]
        print(f"  Cookie names: {cookie_names[:15]}")

        # Check full rehydration structure
        print("\n🔍 Full rehydration analysis...")
        rehydration = await page.evaluate("""() => {
            try {
                const el = document.querySelector('#__UNIVERSAL_DATA_FOR_REHYDRATION__');
                if (!el) return null;
                const data = JSON.parse(el.textContent);
                const scope = data.__DEFAULT_SCOPE__ || {};
                const vd = scope['webapp.video-detail'] || {};
                
                // Get full itemStruct keys
                const item = vd?.itemInfo?.itemStruct || {};
                const itemKeys = Object.keys(item);
                
                // Get full statusMsg
                const statusMsg = vd?.statusMsg;
                const statusCode = vd?.statusCode;
                
                return {
                    scopeKeys: Object.keys(scope),
                    statusCode,
                    statusMsg,
                    itemKeys,
                    videoId: item?.id,
                    desc: (item?.desc || '').substring(0, 200),
                    anchorCount: (item?.anchors || []).length,
                    anchorTypes: item?.anchorTypes || [],
                    
                    // Check if there's author info (would prove the struct has data)
                    authorId: item?.author?.id,
                    authorName: item?.author?.uniqueId,
                    
                    // Stats
                    diggCount: item?.stats?.diggCount,
                    playCount: item?.stats?.playCount,
                    
                    // Full video-detail keys
                    videoDetailKeys: Object.keys(vd),
                    
                    // Check shareMeta
                    shareMeta: vd?.shareMeta,
                };
            } catch(e) { return {error: e.message}; }
        }""")
        
        if rehydration:
            print(json.dumps(rehydration, indent=2, default=str)[:2000])
        else:
            print("  No rehydration found")

        # Now try to use the page's JS APIs to call TikTok's internal functions
        print("\n🔌 Trying internal API calls via page context...")
        
        # Method 1: Try /api/item/detail endpoint
        detail_url = f"https://www.tiktok.com/api/item/detail/?WebIdLastTime=1774786000&aid=1988&itemId={VIDEO_ID}"
        print(f"\n  Trying: /api/item/detail/?itemId={VIDEO_ID}")
        
        try:
            resp = await page.evaluate(f"""async () => {{
                try {{
                    const r = await fetch('{detail_url}', {{
                        credentials: 'include',
                        headers: {{ 'Accept': 'application/json' }}
                    }});
                    const t = await r.text();
                    return {{ status: r.status, size: t.length, body: t.substring(0, 5000) }};
                }} catch(e) {{ return {{ error: e.message }}; }}
            }}""")
            print(f"    Status: {resp.get('status')}, Size: {resp.get('size')}")
            if resp.get('body'):
                try:
                    data = json.loads(resp['body'])
                    item = data.get('itemInfo', {}).get('itemStruct', {})
                    print(f"    itemStruct keys: {list(item.keys())[:20]}")
                    print(f"    id: {item.get('id')}")
                    print(f"    desc: {item.get('desc', '')[:100]}")
                    print(f"    anchors: {len(item.get('anchors', []))}")
                    print(f"    anchorTypes: {item.get('anchorTypes', [])}")
                    
                    if item.get('anchors'):
                        for i, a in enumerate(item['anchors'][:3]):
                            print(f"\n    Anchor [{i}]:")
                            print(f"      keys: {list(a.keys())}")
                            extra_raw = a.get('extra', '')
                            try:
                                extra_list = json.loads(extra_raw)
                                if isinstance(extra_list, list):
                                    for j, ea in enumerate(extra_list[:2]):
                                        inner = json.loads(ea.get('extra', '{}'))
                                        print(f"      Product [{j}]: id={inner.get('product_id')}, title={inner.get('title','')[:50]}, price={inner.get('price')}")
                            except:
                                print(f"      extra parse fail: {str(extra_raw)[:80]}")
                except json.JSONDecodeError:
                    print(f"    Response (not JSON): {resp['body'][:300]}")
            if resp.get('error'):
                print(f"    Error: {resp['error']}")
        except Exception as e:
            print(f"    Exception: {e}")

        # Method 2: Try /api/related/item_list endpoint
        related_url = f"https://www.tiktok.com/api/related/item_list/?WebIdLastTime=1774786000&aid=1988&itemID={VIDEO_ID}&count=20"
        print(f"\n  Trying: /api/related/item_list/?itemID={VIDEO_ID}")
        try:
            resp2 = await page.evaluate(f"""async () => {{
                try {{
                    const r = await fetch('{related_url}', {{
                        credentials: 'include',
                        headers: {{ 'Accept': 'application/json' }}
                    }});
                    const t = await r.text();
                    return {{ status: r.status, size: t.length, body: t.substring(0, 5000) }};
                }} catch(e) {{ return {{ error: e.message }}; }}
            }}""")
            print(f"    Status: {resp2.get('status')}, Size: {resp2.get('size')}")
            if resp2.get('body'):
                try:
                    data2 = json.loads(resp2['body'])
                    items = data2.get('itemList', [])
                    print(f"    items: {len(items)}")
                    for it in items[:5]:
                        anchors = it.get('anchors', [])
                        print(f"      id={it.get('id')}, anchors={len(anchors)}, desc={it.get('desc','')[:50]}")
                except json.JSONDecodeError:
                    print(f"    Not JSON: {resp2['body'][:200]}")
        except Exception as e:
            print(f"    Exception: {e}")

        # Method 3: Try /api/comment/list to verify API access works
        comment_url = f"https://www.tiktok.com/api/comment/list/?WebIdLastTime=1774786000&aid=1988&aweme_id={VIDEO_ID}&count=5"
        print(f"\n  Trying: /api/comment/list/?aweme_id={VIDEO_ID}")
        try:
            resp3 = await page.evaluate(f"""async () => {{
                try {{
                    const r = await fetch('{comment_url}', {{
                        credentials: 'include',
                        headers: {{ 'Accept': 'application/json' }}
                    }});
                    const t = await r.text();
                    return {{ status: r.status, size: t.length, body: t.substring(0, 3000) }};
                }} catch(e) {{ return {{ error: e.message }}; }}
            }}""")
            print(f"    Status: {resp3.get('status')}, Size: {resp3.get('size')}")
            if resp3.get('body'):
                try:
                    data3 = json.loads(resp3['body'])
                    comments = data3.get('comments', [])
                    print(f"    comments: {len(comments)}")
                    print(f"    status_code: {data3.get('status_code')}")
                except json.JSONDecodeError:
                    print(f"    Not JSON: {resp3['body'][:200]}")
        except Exception as e:
            print(f"    Exception: {e}")

        print(f"\n  Total passive API responses captured: {len(api_responses)}")
        for url, info in list(api_responses.items())[:10]:
            print(f"    {url[:100]} ({info['size']:,}B)")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
