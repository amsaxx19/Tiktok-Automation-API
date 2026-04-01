#!/usr/bin/env python3
"""
Desktop UA + Playwright = get hydration data with anchors.
Then dump COMPLETE product data to find ALL available fields.
"""
import asyncio, json, re, os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
PROXY = os.getenv("PROXY_URL", "")
VIDEO_URL = "https://www.tiktok.com/@amosthiosa/video/7622303845783309575"
OUT_DIR = Path(__file__).resolve().parent / "price_final4"
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
        # Use DESKTOP user agent
        ctx = await browser.new_context(
            proxy=pw_proxy,
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="id-ID",
            viewport={"width": 1920, "height": 1080},
        )
        page = await ctx.new_page()

        # Load video
        print(f"📱 Loading: {VIDEO_URL}", flush=True)
        try:
            await page.goto(VIDEO_URL, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"  Nav: {e}", flush=True)
        await page.wait_for_timeout(8000)
        print(f"  Title: {await page.title()}", flush=True)

        # Extract anchor data - desktop UA should have hydration data
        print("\n📋 Extracting anchor data...", flush=True)
        result = await page.evaluate(r"""() => {
            try {
                const ud = window.__UNIVERSAL_DATA_FOR_REHYDRATION__;
                if (!ud) return {error: "No UNIVERSAL_DATA"};
                const ds = ud['__DEFAULT_SCOPE__'] || {};
                const keys = Object.keys(ds);
                
                const vid = ds['webapp.video-detail'];
                if (!vid) return {error: "No video-detail", scopeKeys: keys};
                
                const struct = vid.itemInfo?.itemStruct;
                if (!struct) return {error: "No itemStruct"};
                
                const anchors = struct.anchors || [];
                if (anchors.length === 0) return {error: "No anchors"};
                
                // Return COMPLETE first anchor JSON
                return {
                    ok: true,
                    count: anchors.length,
                    firstRaw: JSON.stringify(anchors[0]),
                };
            } catch(e) {
                return {error: e.message};
            }
        }""")
        
        if result.get("error"):
            print(f"  Error: {result}", flush=True)
            await browser.close()
            return
        
        print(f"  Anchors: {result['count']}", flush=True)
        
        # Parse first anchor completely
        anc = json.loads(result["firstRaw"])
        
        # Save raw anchor
        with open(OUT_DIR / "anchor_raw.json", "w") as f:
            json.dump(anc, f, indent=2, ensure_ascii=False)
        
        print(f"\n  TOP-LEVEL ANCHOR KEYS: {sorted(anc.keys())}", flush=True)
        for k in sorted(anc.keys()):
            v = anc[k]
            if k == "extra":
                print(f"  {k} = <string, {len(v)} chars>", flush=True)
            elif isinstance(v, (str, int, float, bool, type(None))):
                print(f"  {k} = {v}", flush=True)
            elif isinstance(v, dict):
                print(f"  {k} = {json.dumps(v, ensure_ascii=False)[:200]}", flush=True)
            elif isinstance(v, list):
                print(f"  {k} = [{len(v)} items]", flush=True)
        
        # Parse extra -> outer[0] -> inner extra (product)
        extra_str = anc.get("extra", "")
        if extra_str:
            outer = json.loads(extra_str)
            if isinstance(outer, list) and outer:
                entry = outer[0]
                print(f"\n  EXTRA[0] KEYS: {sorted(entry.keys())}", flush=True)
                for k in sorted(entry.keys()):
                    v = entry[k]
                    if k == "extra":
                        print(f"    {k} = <string, {len(v)} chars>", flush=True)
                    elif isinstance(v, (str, int, float, bool, type(None))):
                        print(f"    {k} = {v}", flush=True)
                    elif isinstance(v, dict):
                        print(f"    {k} = {json.dumps(v, ensure_ascii=False)[:200]}", flush=True)
                    elif isinstance(v, list):
                        print(f"    {k} = [{len(v)} items]", flush=True)
                
                inner_str = entry.get("extra", "")
                if inner_str:
                    prod = json.loads(inner_str)
                    
                    # SAVE COMPLETE PRODUCT JSON
                    with open(OUT_DIR / "product_complete.json", "w") as f:
                        json.dump(prod, f, indent=2, ensure_ascii=False)
                    print(f"\n  💾 Saved complete product JSON to {OUT_DIR / 'product_complete.json'}", flush=True)
                    
                    print(f"\n  PRODUCT DATA - {len(prod)} KEYS:", flush=True)
                    for k in sorted(prod.keys()):
                        v = prod[k]
                        if isinstance(v, (str, int, float, bool, type(None))):
                            print(f"    {k} = {v}", flush=True)
                        elif isinstance(v, dict):
                            print(f"    {k} = {json.dumps(v, ensure_ascii=False)[:200]}", flush=True)
                        elif isinstance(v, list):
                            print(f"    {k} = [{len(v)} items]", flush=True)
                            for idx, item in enumerate(v[:2]):
                                if isinstance(item, dict):
                                    print(f"      [{idx}] keys={sorted(item.keys())}", flush=True)
                                    for sk in sorted(item.keys()):
                                        sv = item[sk]
                                        if isinstance(sv, (str, int, float, bool, type(None))):
                                            print(f"        {sk} = {sv}", flush=True)
                                elif isinstance(item, str):
                                    print(f"      [{idx}] = {item[:100]}", flush=True)

        # Also get ALL anchors' products for comparison
        print("\n\n📊 Getting ALL anchors' product summaries...", flush=True)
        all_anchors = await page.evaluate(r"""() => {
            const ud = window.__UNIVERSAL_DATA_FOR_REHYDRATION__;
            const ds = ud['__DEFAULT_SCOPE__'] || {};
            const vid = ds['webapp.video-detail'];
            const struct = vid.itemInfo.itemStruct;
            return struct.anchors.map(a => JSON.stringify(a));
        }""")
        
        for ai, raw in enumerate(all_anchors):
            anc = json.loads(raw)
            extra_str = anc.get("extra", "")
            if not extra_str:
                continue
            outer = json.loads(extra_str)
            if isinstance(outer, list) and outer:
                entry = outer[0]
                inner_str = entry.get("extra", "")
                if inner_str:
                    prod = json.loads(inner_str)
                    title = prod.get("title", "?")[:50]
                    price = prod.get("price", "?")
                    mp = prod.get("market_price", "?")
                    sc = prod.get("sold_count", "?")
                    sn = prod.get("shop_name", "?")
                    source = prod.get("source", "?")
                    print(f"  [{ai}] {title} | price={price} market_price={mp} sold={sc} shop={sn} source={source}", flush=True)

        await browser.close()
        print("\n✅ Done", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
