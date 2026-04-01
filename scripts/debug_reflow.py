"""Debug: capture reflow API response and analyze its structure."""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

VIDEO_URL = "https://www.tiktok.com/@amosthiosa/video/7507723498614331653"
PROXY_URL = os.getenv("PROXY_URL", "http://5sjQhR7dWXPoSuv:gAbLujfGLSP2rWU@178.93.21.156:49644")

async def main():
    from playwright.async_api import async_playwright

    reflow_data = {}
    rehydration_data = {}

    async def on_response(response):
        nonlocal reflow_data
        url = response.url
        if "/api/reflow/recommend/item_list" in url and response.status == 200:
            try:
                body = await response.body()
                reflow_data = json.loads(body)
                print(f"✅ Intercepted reflow API ({len(body):,}B)")
            except Exception as e:
                print(f"❌ Failed to parse reflow: {e}")

    proxy_config = {
        "server": "http://178.93.21.156:49644",
        "username": "5sjQhR7dWXPoSuv",
        "password": "gAbLujfGLSP2rWU",
    }

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            proxy=proxy_config,
            locale='id-ID',
            timezone_id='Asia/Jakarta',
            viewport={'width': 412, 'height': 915},
            user_agent='Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36',
            extra_http_headers={'Accept-Language': 'id-ID,id;q=0.9,en;q=0.5'},
        )
        page = await context.new_page()
        page.on("response", on_response)

        print(f"📄 Navigating to {VIDEO_URL}")
        try:
            await page.goto(VIDEO_URL, wait_until='domcontentloaded', timeout=60000)
        except Exception as e:
            print(f"⚠️ Navigation: {e}")
        await page.wait_for_timeout(5000)

        # Try clicking shop anchors
        if not reflow_data:
            for sel in ['a[href*="shop-id."]', 'a[href*="tokopedia.com/pdp/"]',
                        '[class*="EcomAnchor"]', '[class*="ecom-anchor"]',
                        '[class*="product-anchor"]']:
                try:
                    loc = page.locator(sel)
                    cnt = await loc.count()
                    if cnt > 0:
                        print(f"  Clicking {sel} ({cnt} found)...")
                        await loc.first.click(force=True, timeout=3000)
                        await page.wait_for_timeout(5000)
                        if reflow_data:
                            break
                except Exception:
                    pass

        # Scroll to trigger lazy loads
        if not reflow_data:
            for i in range(3):
                await page.evaluate(f"window.scrollBy(0, {400*(i+1)})")
                await page.wait_for_timeout(2000)
                if reflow_data:
                    break

        # Extract rehydration data from page scripts
        print("\n🔍 Extracting rehydration data from page scripts...")
        script_data = await page.evaluate("""() => {
            try {
                const scripts = document.querySelectorAll('script');
                for (const s of scripts) {
                    const t = s.textContent || '';
                    if (s.id === '__UNIVERSAL_DATA_FOR_REHYDRATION__' && t.length > 100) {
                        return JSON.stringify({_rehydration: true, _data: JSON.parse(t)});
                    }
                    if (t.includes('"videoDetail"') || t.includes('"itemInfo"')) {
                        return t.substring(0, 300000);
                    }
                }
            } catch(e) { return JSON.stringify({error: e.message}); }
            return null;
        }""")

        if script_data:
            sd = json.loads(script_data)
            if sd.get("_rehydration"):
                scope = sd["_data"].get("__DEFAULT_SCOPE__", {})
                video_detail = scope.get("webapp.video-detail", {})
                item = video_detail.get("itemInfo", {}).get("itemStruct", {})
                print(f"  Rehydration item ID: {item.get('id')}")
                print(f"  Rehydration desc: {item.get('desc', '')[:100]}")
                anchors = item.get("anchors", [])
                print(f"  Rehydration anchors: {len(anchors)}")
                anchor_types = item.get("anchorTypes", [])
                print(f"  Rehydration anchorTypes: {anchor_types}")
                if anchors:
                    for i, a in enumerate(anchors[:3]):
                        print(f"\n  Anchor [{i}] keys: {list(a.keys())}")
                        extra_raw = a.get("extra", "")
                        try:
                            extra_list = json.loads(extra_raw) if isinstance(extra_raw, str) else extra_raw
                            if isinstance(extra_list, list):
                                for j, ea in enumerate(extra_list[:2]):
                                    inner = ea.get("extra", "{}")
                                    try:
                                        pd = json.loads(inner) if isinstance(inner, str) else inner
                                        print(f"    Product [{j}]: id={pd.get('product_id')}, title={pd.get('title','')[:60]}, price={pd.get('price')}")
                                    except:
                                        print(f"    Inner parse failed: {str(inner)[:100]}")
                        except:
                            print(f"    Extra parse failed: {str(extra_raw)[:100]}")
                
                # Also check what other keys are in scope
                print(f"\n  __DEFAULT_SCOPE__ keys: {list(scope.keys())}")
            else:
                # Legacy format
                item = sd.get("videoDetail", {}).get("itemInfo", {}).get("itemStruct", {})
                print(f"  Legacy item ID: {item.get('id')}")
                print(f"  Legacy anchors: {len(item.get('anchors', []))}")
        else:
            print("  ❌ No rehydration script found")

        # Now analyze reflow data
        print(f"\n{'='*60}")
        print("📊 REFLOW DATA ANALYSIS")
        print(f"{'='*60}")

        if not reflow_data:
            print("❌ No reflow data captured")
        else:
            item_list = reflow_data.get("item_list", [])
            print(f"Total items in reflow: {len(item_list)}")
            
            target_video_id = "7507723498614331653"
            
            for i, item in enumerate(item_list[:5]):  # Show first 5
                basic = item.get("item_basic", {})
                vid_id = basic.get("id", "")
                desc = basic.get("desc", "")[:60]
                anchors = basic.get("anchors", [])
                anchor_types = basic.get("anchor_types", basic.get("anchorTypes", []))
                is_target = str(vid_id) == target_video_id
                marker = " <<<< TARGET" if is_target else ""
                
                print(f"\n  Item [{i}]: id={vid_id}{marker}")
                print(f"    desc: {desc}")
                print(f"    anchors: {len(anchors)}")
                print(f"    anchor_types: {anchor_types}")
                
                if anchors:
                    for ai, a in enumerate(anchors[:2]):
                        print(f"    Anchor [{ai}] keys: {list(a.keys())}")
                        extra_raw = a.get("extra", "")
                        if isinstance(extra_raw, str):
                            try:
                                extra_list = json.loads(extra_raw)
                                if isinstance(extra_list, list):
                                    print(f"      extra is list with {len(extra_list)} items")
                                    for ei, ea in enumerate(extra_list[:2]):
                                        inner = ea.get("extra", "{}")
                                        try:
                                            pd = json.loads(inner) if isinstance(inner, str) else inner
                                            print(f"      Product [{ei}]: id={pd.get('product_id')}, title={pd.get('title','')[:50]}, price={pd.get('price')}")
                                        except:
                                            print(f"      Inner parse fail: {str(inner)[:80]}")
                                elif isinstance(extra_list, dict):
                                    print(f"      extra is dict with keys: {list(extra_list.keys())[:10]}")
                            except json.JSONDecodeError:
                                print(f"      extra is not JSON: {extra_raw[:80]}")
                        elif isinstance(extra_raw, (list, dict)):
                            print(f"      extra is {type(extra_raw).__name__}: {str(extra_raw)[:100]}")

            # Count how many items have product anchors
            items_with_products = 0
            total_products = 0
            for item in item_list:
                basic = item.get("item_basic", {})
                anchors = basic.get("anchors", [])
                if anchors:
                    items_with_products += 1
                    for a in anchors:
                        extra_raw = a.get("extra", "")
                        try:
                            el = json.loads(extra_raw) if isinstance(extra_raw, str) else extra_raw
                            if isinstance(el, list):
                                total_products += len(el)
                        except:
                            pass

            print(f"\n📈 Summary:")
            print(f"  Items with product anchors: {items_with_products}/{len(item_list)}")
            print(f"  Total product references: {total_products}")
            print(f"  Target video in list: {any(str(it.get('item_basic',{}).get('id','')) == target_video_id for it in item_list)}")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
