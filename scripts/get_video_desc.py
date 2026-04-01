"""Quick: fetch video desc from reflow for the live video."""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

VIDEO_URL = "https://www.tiktok.com/@amosthiosa/video/7622536313668979976"
VIDEO_ID = "7622536313668979976"

async def main():
    from playwright.async_api import async_playwright
    reflow_data = {}
    
    async def on_response(response):
        nonlocal reflow_data
        if "/api/reflow/recommend/item_list" in response.url and response.status == 200:
            try:
                body = await response.body()
                reflow_data = json.loads(body)
            except: pass

    proxy = {
        "server": "http://178.93.21.156:49644",
        "username": "5sjQhR7dWXPoSuv",
        "password": "gAbLujfGLSP2rWU",
    }
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            proxy=proxy, locale='id-ID', timezone_id='Asia/Jakarta',
            viewport={'width': 412, 'height': 915},
            user_agent='Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/125.0 Mobile Safari/537.36',
            extra_http_headers={'Accept-Language': 'id-ID,id;q=0.9'},
        )
        page = await context.new_page()
        page.on("response", on_response)
        
        try:
            await page.goto(VIDEO_URL, wait_until='domcontentloaded', timeout=30000)
        except: pass
        await page.wait_for_timeout(5000)
        
        # click to trigger reflow
        for sel in ['a[href*="shop-id."]', '[class*="EcomAnchor"]', '[class*="ecom-anchor"]']:
            try:
                loc = page.locator(sel)
                if await loc.count() > 0:
                    await loc.first.click(force=True, timeout=3000)
                    await page.wait_for_timeout(5000)
                    if reflow_data: break
            except: pass
        
        await browser.close()
    
    if not reflow_data:
        print("No reflow captured"); return
    
    for item in reflow_data.get("item_list", []):
        basic = item.get("item_basic", {})
        vid_id = str(basic.get("id", ""))
        if vid_id == VIDEO_ID:
            desc = basic.get("desc", "")
            print(f"TARGET VIDEO DESC:\n{desc}\n")
            
            anchors = basic.get("anchors", [])
            for i, a in enumerate(anchors):
                try:
                    extra_list = json.loads(a.get("extra", "[]"))
                    for ea in extra_list:
                        inner = json.loads(ea.get("extra", "{}"))
                        print(f"  Product [{i}]: {inner.get('title','')[:80]}")
                        print(f"    price={inner.get('price')}, market_price={inner.get('market_price')}")
                        print(f"    seo_url={inner.get('seo_url','')[:100]}")
                except: pass
            break
    else:
        # List all video IDs
        for item in reflow_data.get("item_list", [])[:10]:
            basic = item.get("item_basic", {})
            print(f"  vid={basic.get('id')} desc={basic.get('desc','')[:60]}")

asyncio.run(main())
