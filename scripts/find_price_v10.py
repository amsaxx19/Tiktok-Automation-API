#!/usr/bin/env python3
"""
Try multiple approaches to find video hydration data on TikTok page.
Check SIGI_STATE, __UNIVERSAL_DATA_FOR_REHYDRATION__, inline scripts, etc.
Wait longer for client-side hydration to complete.
"""
import asyncio, json, os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
PROXY = os.getenv("PROXY_URL", "")
VIDEO_URL = "https://www.tiktok.com/@amosthiosa/video/7622303845783309575"
OUT_DIR = Path(__file__).resolve().parent / "price_final5"
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
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="id-ID",
            viewport={"width": 1920, "height": 1080},
        )
        page = await ctx.new_page()

        # Load
        print(f"Loading: {VIDEO_URL}", flush=True)
        try:
            await page.goto(VIDEO_URL, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"  Nav: {e}", flush=True)
        
        # Wait and check multiple times
        for wait_s in [5, 10, 15]:
            print(f"\n  Waiting {wait_s}s total...", flush=True)
            await page.wait_for_timeout(5000)
            
            result = await page.evaluate(r"""() => {
                const out = {};
                
                // Check __UNIVERSAL_DATA_FOR_REHYDRATION__
                if (window.__UNIVERSAL_DATA_FOR_REHYDRATION__) {
                    const ud = window.__UNIVERSAL_DATA_FOR_REHYDRATION__;
                    const ds = ud['__DEFAULT_SCOPE__'] || {};
                    out.universal_scope_keys = Object.keys(ds);
                    if (ds['webapp.video-detail']) {
                        const vid = ds['webapp.video-detail'];
                        out.has_video_detail = true;
                        const struct = vid.itemInfo?.itemStruct;
                        if (struct) {
                            out.has_struct = true;
                            out.anchor_count = (struct.anchors || []).length;
                        }
                    }
                } else {
                    out.no_universal_data = true;
                }
                
                // Check SIGI_STATE
                if (window.SIGI_STATE) {
                    out.sigi_keys = Object.keys(window.SIGI_STATE);
                    const im = window.SIGI_STATE.ItemModule;
                    if (im) {
                        out.sigi_items = Object.keys(im);
                        // Check first item for anchors
                        const firstKey = Object.keys(im)[0];
                        if (firstKey) {
                            const item = im[firstKey];
                            out.sigi_first_anchor_count = (item.anchors || []).length;
                        }
                    }
                } else {
                    out.no_sigi = true;
                }
                
                // Check __NEXT_DATA__
                if (window.__NEXT_DATA__) {
                    out.has_next_data = true;
                }
                
                // Check for script tags with anchor data
                const scripts = document.querySelectorAll('script[id="__UNIVERSAL_DATA_FOR_REHYDRATION__"]');
                out.rehydration_script_count = scripts.length;
                if (scripts.length > 0) {
                    const text = scripts[0].textContent || '';
                    out.rehydration_script_size = text.length;
                    // Check if it has anchors
                    out.rehydration_has_anchors = text.includes('"anchors"');
                    out.rehydration_has_price = text.includes('"price"');
                    // Try to parse
                    try {
                        const data = JSON.parse(text);
                        const ds2 = data['__DEFAULT_SCOPE__'] || {};
                        out.rehydration_parsed_keys = Object.keys(ds2);
                    } catch(e) {}
                }
                
                return out;
            }""")
            
            print(f"  State at {wait_s}s: {json.dumps(result, indent=2)}", flush=True)
            
            if result.get("anchor_count", 0) > 0 or result.get("sigi_first_anchor_count", 0) > 0:
                print("  ✅ Anchors found!", flush=True)
                break
        
        # Check the actual HTML for embedded JSON
        print("\n\n📋 Checking raw HTML for embedded data...", flush=True)
        html = await page.content()
        print(f"  HTML size: {len(html)} bytes", flush=True)
        
        # Save HTML
        with open(OUT_DIR / "page.html", "w") as f:
            f.write(html)
        
        # Find script tags with rehydration data
        import re
        rehydration_scripts = re.findall(
            r'<script[^>]*id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
            html, re.DOTALL
        )
        print(f"  Rehydration scripts found: {len(rehydration_scripts)}", flush=True)
        
        if rehydration_scripts:
            raw = rehydration_scripts[0]
            print(f"  Rehydration script size: {len(raw)} chars", flush=True)
            
            # Save it
            with open(OUT_DIR / "rehydration_raw.json", "w") as f:
                f.write(raw)
            
            try:
                data = json.loads(raw)
                ds = data.get("__DEFAULT_SCOPE__", {})
                print(f"  DEFAULT_SCOPE keys: {list(ds.keys())}", flush=True)
                
                vid = ds.get("webapp.video-detail", {})
                if vid:
                    struct = vid.get("itemInfo", {}).get("itemStruct", {})
                    anchors = struct.get("anchors", [])
                    print(f"  Anchors in HTML: {len(anchors)}", flush=True)
                    
                    if anchors:
                        # Parse first anchor
                        anc = anchors[0]
                        extra_str = anc.get("extra", "")
                        if extra_str:
                            outer = json.loads(extra_str)
                            if isinstance(outer, list) and outer:
                                inner_str = outer[0].get("extra", "")
                                if inner_str:
                                    prod = json.loads(inner_str)
                                    # Save complete product
                                    with open(OUT_DIR / "product_complete.json", "w") as f:
                                        json.dump(prod, f, indent=2, ensure_ascii=False)
                                    
                                    print(f"\n  PRODUCT - ALL KEYS ({len(prod)}):")
                                    for k in sorted(prod.keys()):
                                        v = prod[k]
                                        if isinstance(v, (str, int, float, bool, type(None))):
                                            print(f"    {k} = {v}", flush=True)
                                        elif isinstance(v, dict):
                                            print(f"    {k} = {json.dumps(v, ensure_ascii=False)[:200]}", flush=True)
                                        elif isinstance(v, list):
                                            print(f"    {k} = [{len(v)} items]", flush=True)
                                            for i, item in enumerate(v[:2]):
                                                if isinstance(item, dict):
                                                    print(f"      [{i}]: {json.dumps(item, ensure_ascii=False)[:200]}", flush=True)
            except json.JSONDecodeError as e:
                print(f"  JSON parse error: {e}", flush=True)
                # Show first 500 chars
                print(f"  First 500 chars: {raw[:500]}", flush=True)
        else:
            # Try SIGI_STATE in script
            sigi_scripts = re.findall(
                r'<script[^>]*id="SIGI_STATE"[^>]*>(.*?)</script>',
                html, re.DOTALL
            )
            print(f"  SIGI_STATE scripts: {len(sigi_scripts)}", flush=True)
            
            # Try any script with "anchors"
            anchor_scripts = re.findall(
                r'<script[^>]*>(.*?)</script>',
                html, re.DOTALL
            )
            for i, s in enumerate(anchor_scripts):
                if '"anchors"' in s and '"extra"' in s and len(s) > 1000:
                    print(f"  Script [{i}] has anchors! Size: {len(s)}", flush=True)
                    with open(OUT_DIR / f"anchor_script_{i}.json", "w") as f:
                        f.write(s)
                    break

        await browser.close()
        print("\n✅ Done", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
