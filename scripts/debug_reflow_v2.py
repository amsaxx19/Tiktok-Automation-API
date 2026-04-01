"""Debug: try different approaches to get video detail with anchors."""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

VIDEO_URL = "https://www.tiktok.com/@amosthiosa/video/7507723498614331653"

PROXY = {
    "server": "http://178.93.21.156:49644",
    "username": "5sjQhR7dWXPoSuv",
    "password": "gAbLujfGLSP2rWU",
}

# Desktop UA
DESKTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
MOBILE_UA = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36"


async def try_approach(name, ua, viewport, locale, extra_headers=None, mobile=False):
    from playwright.async_api import async_playwright

    reflow_data = {}
    all_responses = []

    async def on_response(response):
        nonlocal reflow_data
        url = response.url
        # Track ALL API responses
        if "/api/" in url and response.status == 200:
            try:
                body = await response.body()
                all_responses.append({"url": url[:120], "size": len(body)})
                if "/api/reflow/recommend/item_list" in url:
                    reflow_data = json.loads(body)
            except:
                pass

    print(f"\n{'='*60}")
    print(f"🧪 Approach: {name}")
    print(f"{'='*60}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx_opts = {
            "proxy": PROXY,
            "locale": locale,
            "viewport": viewport,
            "user_agent": ua,
            "extra_http_headers": extra_headers or {'Accept-Language': 'id-ID,id;q=0.9'},
        }
        if mobile:
            ctx_opts["is_mobile"] = True
        
        context = await browser.new_context(**ctx_opts)
        page = await context.new_page()
        page.on("response", on_response)

        try:
            await page.goto(VIDEO_URL, wait_until='domcontentloaded', timeout=30000)
        except Exception as e:
            print(f"  Navigation: {e}")
        await page.wait_for_timeout(6000)

        # Check page title
        title = await page.title()
        print(f"  Page title: {title[:80]}")

        # Check for popups / overlays
        popup_text = await page.evaluate("""() => {
            const els = document.querySelectorAll('[class*="modal"], [class*="overlay"], [class*="popup"], [class*="bottom-sheet"]');
            return Array.from(els).map(e => e.textContent?.substring(0, 100) || '').filter(t => t.length > 5);
        }""")
        if popup_text:
            print(f"  Popups found: {len(popup_text)}")
            for pt in popup_text[:3]:
                print(f"    {pt[:80]}")

        # Check rehydration keys
        scope_keys = await page.evaluate("""() => {
            try {
                const scripts = document.querySelectorAll('script#__UNIVERSAL_DATA_FOR_REHYDRATION__');
                if (scripts.length > 0) {
                    const data = JSON.parse(scripts[0].textContent);
                    return Object.keys(data.__DEFAULT_SCOPE__ || {});
                }
            } catch(e) {}
            return [];
        }""")
        print(f"  __DEFAULT_SCOPE__ keys: {scope_keys}")

        has_video_detail = "webapp.video-detail" in scope_keys
        print(f"  Has webapp.video-detail: {has_video_detail}")

        if has_video_detail:
            # Extract anchor count
            anchor_info = await page.evaluate("""() => {
                try {
                    const scripts = document.querySelectorAll('script#__UNIVERSAL_DATA_FOR_REHYDRATION__');
                    const data = JSON.parse(scripts[0].textContent);
                    const scope = data.__DEFAULT_SCOPE__;
                    const item = scope['webapp.video-detail']?.itemInfo?.itemStruct || {};
                    return {
                        id: item.id,
                        desc: (item.desc || '').substring(0, 100),
                        anchors: (item.anchors || []).length,
                        anchorTypes: item.anchorTypes || [],
                    };
                } catch(e) { return {error: e.message}; }
            }""")
            print(f"  Anchor info: {json.dumps(anchor_info)}")

        # Try clicking to trigger more APIs
        for sel in ['a[href*="shop-id."]', '[class*="EcomAnchor"]', '[class*="ecom-anchor"]']:
            try:
                loc = page.locator(sel)
                cnt = await loc.count()
                if cnt > 0:
                    print(f"  Found {cnt} elements matching {sel}")
                    await loc.first.click(force=True, timeout=3000)
                    await page.wait_for_timeout(3000)
            except:
                pass

        # Summary of API calls
        print(f"  Total API responses: {len(all_responses)}")
        for r in all_responses[:10]:
            print(f"    {r['url'][:100]} ({r['size']:,}B)")

        # Reflow summary
        if reflow_data:
            items = reflow_data.get("item_list", [])
            items_with_anchors = sum(1 for it in items if it.get("item_basic", {}).get("anchors"))
            print(f"  Reflow: {len(items)} items, {items_with_anchors} with anchors")
        else:
            print(f"  Reflow: NOT captured")

        await browser.close()
    return has_video_detail


async def main():
    # Approach 1: Desktop with English locale
    r1 = await try_approach(
        "Desktop EN", DESKTOP_UA,
        {"width": 1920, "height": 1080}, "en-US",
        {"Accept-Language": "en-US,en;q=0.9"},
    )

    # Approach 2: Desktop with Indonesian locale
    r2 = await try_approach(
        "Desktop ID", DESKTOP_UA,
        {"width": 1920, "height": 1080}, "id-ID",
        {"Accept-Language": "id-ID,id;q=0.9,en;q=0.5"},
    )

    # Approach 3: Mobile with is_mobile=True
    r3 = await try_approach(
        "Mobile (is_mobile)", MOBILE_UA,
        {"width": 412, "height": 915}, "id-ID",
        {"Accept-Language": "id-ID,id;q=0.9,en;q=0.5"},
        mobile=True,
    )

    # Approach 4: Desktop without proxy — need separate function
    r4 = False  # skip for now, proxy is hardcoded in try_approach

    print(f"\n{'='*60}")
    print(f"📊 RESULTS")
    print(f"  Desktop EN:    video-detail={'✅' if r1 else '❌'}")
    print(f"  Desktop ID:    video-detail={'✅' if r2 else '❌'}")
    print(f"  Mobile ID:     video-detail={'✅' if r3 else '❌'}")
    print(f"  Desktop NOPROXY: video-detail={'✅' if r4 else '❌'}")


if __name__ == "__main__":
    asyncio.run(main())
