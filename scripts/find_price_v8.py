#!/usr/bin/env python3
"""
Use regular Playwright (not patchright) which successfully extracts anchor data.
Focus on:
1. Getting ALL anchor data with EVERY field dumped
2. Making in-page API calls with proper URL params (not just product_id)
3. Intercepting the actual product detail request that happens when clicking product
"""
import asyncio, json, re, os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
PROXY = os.getenv("PROXY_URL", "")
VIDEO_URL = "https://www.tiktok.com/@amosthiosa/video/7622303845783309575"
OUT_DIR = Path(__file__).resolve().parent / "price_final3"
OUT_DIR.mkdir(exist_ok=True)


async def main():
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
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
            locale="id-ID",
            viewport={"width": 390, "height": 844},
            is_mobile=True,
            has_touch=True,
        )
        page = await ctx.new_page()

        # Capture ALL network responses
        resp_idx = [0]

        async def on_response(response):
            url = response.url
            ct = response.headers.get("content-type", "")
            if response.status != 200:
                return
            if "json" not in ct:
                return
            try:
                body = await response.text()
            except:
                return
            if len(body) < 50:
                return
            
            i = resp_idx[0]
            resp_idx[0] += 1
            fname = OUT_DIR / f"resp_{i:03d}.json"
            with open(fname, "w") as f:
                f.write(body)

        page.on("response", on_response)

        # Load video
        print(f"📱 Loading: {VIDEO_URL}", flush=True)
        try:
            await page.goto(VIDEO_URL, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"  Nav error: {e}", flush=True)
        await page.wait_for_timeout(6000)
        print(f"  Title: {await page.title()}", flush=True)

        # Extract COMPLETE anchor data - dump EVERYTHING
        print("\n📋 Dumping ALL anchor data...", flush=True)
        result = await page.evaluate(r"""() => {
            try {
                const ud = window.__UNIVERSAL_DATA_FOR_REHYDRATION__;
                if (!ud) return {error: "No UNIVERSAL_DATA"};
                const ds = ud['__DEFAULT_SCOPE__'] || {};
                const keys = Object.keys(ds);
                
                // Find video detail
                const vid = ds['webapp.video-detail'];
                if (!vid) return {error: "No video-detail", availableKeys: keys};
                
                const struct = vid.itemInfo?.itemStruct;
                if (!struct) return {error: "No itemStruct"};
                
                const anchors = struct.anchors || [];
                
                // Return complete raw JSON of first anchor for analysis
                return {
                    anchorCount: anchors.length,
                    // Raw stringified first anchor
                    firstAnchorRaw: anchors.length > 0 ? JSON.stringify(anchors[0]) : null,
                    // All anchor types and keywords
                    summary: anchors.map(a => ({type: a.type, keyword: a.keyword, id: a.id})),
                };
            } catch(e) {
                return {error: e.message, stack: e.stack};
            }
        }""")
        
        print(f"  Result: {json.dumps(result, indent=2, ensure_ascii=False)[:500]}", flush=True)
        
        if result.get("firstAnchorRaw"):
            # Parse and dump the complete anchor
            first_anc = json.loads(result["firstAnchorRaw"])
            print(f"\n  First anchor TOP-LEVEL KEYS: {sorted(first_anc.keys())}", flush=True)
            
            # Save complete anchor JSON for analysis
            with open(OUT_DIR / "first_anchor.json", "w") as f:
                f.write(result["firstAnchorRaw"])
            
            # Parse extra chain
            extra_str = first_anc.get("extra", "")
            if extra_str:
                outer = json.loads(extra_str)
                if isinstance(outer, list) and outer:
                    entry = outer[0]
                    print(f"\n  extra[0] KEYS: {sorted(entry.keys())}", flush=True)
                    for k, v in sorted(entry.items()):
                        if k != "extra":
                            print(f"    {k} = {json.dumps(v, ensure_ascii=False)[:200]}", flush=True)
                    
                    inner_str = entry.get("extra", "")
                    if inner_str:
                        prod = json.loads(inner_str)
                        print(f"\n  PRODUCT DATA - ALL {len(prod)} KEYS:", flush=True)
                        
                        # Save complete product JSON
                        with open(OUT_DIR / "product_data.json", "w") as f:
                            json.dump(prod, f, indent=2, ensure_ascii=False)
                        print(f"  Saved to {OUT_DIR / 'product_data.json'}", flush=True)
                        
                        # Dump EVERY field
                        for k in sorted(prod.keys()):
                            v = prod[k]
                            if isinstance(v, (str, int, float, bool, type(None))):
                                print(f"    {k} = {v}", flush=True)
                            elif isinstance(v, dict):
                                print(f"    {k} = (dict, {len(v)} keys) {json.dumps(v, ensure_ascii=False)[:150]}", flush=True)
                            elif isinstance(v, list):
                                print(f"    {k} = (list, {len(v)} items)", flush=True)
                                for idx, item in enumerate(v[:2]):
                                    if isinstance(item, dict):
                                        print(f"      [{idx}] = {json.dumps(item, ensure_ascii=False)[:200]}", flush=True)
                                    else:
                                        print(f"      [{idx}] = {item}", flush=True)
        
        # Now try using the browser to make fetch calls to various TikTok APIs
        # The key is to find the RIGHT endpoint and parameters
        print("\n\n🔐 Testing API calls from browser context...", flush=True)
        
        # Get cookies for reference
        cookies = await ctx.cookies()
        print(f"  Cookies: {len(cookies)}", flush=True)
        
        # Try various API endpoints with fetch from inside the page
        api_tests = [
            # Anchor detail
            "/api/anchor/detail/?anchor_id=1732773678384055322&item_id=7622303845783309575",
            # Commerce product 
            "/api/commerce/product/detail/?product_id=1732773678384055322&item_id=7622303845783309575",
            # Reflow with specific video
            "/api/reflow/item/detail/?item_id=7622303845783309575",
            # Shop product info
            "/api/shop/product/?product_id=1732773678384055322",
            # OEC anchor
            "/api/oec/anchor/detail/?anchor_id=1732773678384055322",
            # Video commerce detail
            "/api/video/commerce/detail/?item_id=7622303845783309575",
            # Ecommerce anchor info
            "/api/ecommerce/anchor/info/?anchor_id=1732773678384055322",
        ]
        
        for api_path in api_tests:
            full_url = f"https://www.tiktok.com{api_path}"
            print(f"\n  Fetch: {api_path[:80]}", flush=True)
            try:
                resp = await page.evaluate(f"""async () => {{
                    try {{
                        const r = await fetch("{full_url}", {{
                            credentials: 'include',
                            headers: {{'Accept': 'application/json'}},
                        }});
                        const text = await r.text();
                        return {{status: r.status, body: text.substring(0, 3000)}};
                    }} catch(e) {{
                        return {{error: e.message}};
                    }}
                }}""")
                status = resp.get("status", "?")
                body = resp.get("body", resp.get("error", ""))
                # Check for useful data
                if "price" in body.lower() and '"price":' in body.lower():
                    print(f"    💰 Status={status}, HAS PRICE! Body: {body[:500]}", flush=True)
                elif status != 404:
                    print(f"    Status={status}, Body: {body[:200]}", flush=True)
                else:
                    print(f"    Status=404", flush=True)
            except Exception as e:
                print(f"    Error: {e}", flush=True)

        await browser.close()
        print("\n✅ Done", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
