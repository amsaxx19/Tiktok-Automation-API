#!/usr/bin/env python3
"""
Final approach: Use patchright to load TikTok video page and extract
ALL product anchor data, including ALL fields to find where prices might be.
Also try the TikTok product detail API with cookies from the browser session.
"""
import asyncio, json, re, os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
PROXY = os.getenv("PROXY_URL", "")
VIDEO_URL = "https://www.tiktok.com/@amosthiosa/video/7622303845783309575"
OUT_DIR = Path(__file__).resolve().parent / "price_final2"
OUT_DIR.mkdir(exist_ok=True)


async def main():
    from patchright.async_api import async_playwright

    proxy_parts = PROXY.replace("http://", "").split("@")
    user_pass = proxy_parts[0].split(":")
    host_port = proxy_parts[1].split(":")
    pw_proxy = {
        "server": f"http://{host_port[0]}:{host_port[1]}",
        "username": user_pass[0],
        "password": user_pass[1],
    }

    responses_with_price = []
    all_json_responses = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            proxy=pw_proxy,
            locale="id-ID",
            viewport={"width": 1366, "height": 768},
        )
        page = await ctx.new_page()

        async def on_response(response):
            url = response.url
            ct = response.headers.get("content-type", "")
            if response.status != 200:
                return
            if "json" not in ct:
                return
            # Skip tiny responses
            try:
                body = await response.text()
            except:
                return
            if len(body) < 50:
                return
            
            i = len(all_json_responses)
            all_json_responses.append({"url": url, "size": len(body)})
            
            # Save
            fname = OUT_DIR / f"resp_{i:03d}.json"
            with open(fname, "w") as f:
                f.write(body)
            
            # Check for non-zero price
            if re.search(r'"price"\s*:\s*"?[1-9]', body):
                responses_with_price.append({"i": i, "url": url, "size": len(body), "file": str(fname)})
                print(f"  💰 PRICE HIT #{i}: {url[:100]} ({len(body)}B)", flush=True)

        page.on("response", on_response)

        # Load video
        print(f"📱 Loading: {VIDEO_URL}", flush=True)
        try:
            await page.goto(VIDEO_URL, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"  Nav: {e}", flush=True)
        await page.wait_for_timeout(6000)
        print(f"  Loaded: {await page.title()}", flush=True)

        # Extract ALL anchor data from __UNIVERSAL_DATA_FOR_REHYDRATION__
        print("\n📋 Extracting complete anchor data...", flush=True)
        anchor_dump = await page.evaluate(r"""() => {
            try {
                const ud = window.__UNIVERSAL_DATA_FOR_REHYDRATION__;
                if (!ud) return {error: "No UNIVERSAL_DATA"};
                
                const ds = ud['__DEFAULT_SCOPE__'] || {};
                const vid = ds['webapp.video-detail'];
                if (!vid) return {error: "No video-detail"};
                
                const struct = vid.itemInfo?.itemStruct;
                if (!struct) return {error: "No itemStruct"};
                
                const anchors = struct.anchors || [];
                if (anchors.length === 0) return {error: "No anchors"};
                
                // Return the COMPLETE raw anchor data
                return {
                    anchorCount: anchors.length,
                    // Return each anchor's raw JSON
                    rawAnchors: anchors.map(a => JSON.stringify(a)),
                };
            } catch(e) {
                return {error: e.message};
            }
        }""")
        
        if "error" in anchor_dump:
            print(f"  Error: {anchor_dump['error']}", flush=True)
        else:
            print(f"  Anchors: {anchor_dump['anchorCount']}", flush=True)
            
            # Parse and dump each anchor
            for ai, raw in enumerate(anchor_dump.get("rawAnchors", [])):
                anc = json.loads(raw)
                print(f"\n  Anchor [{ai}]:", flush=True)
                print(f"    type={anc.get('type')}, keyword={anc.get('keyword')}", flush=True)
                print(f"    id={anc.get('id')}", flush=True)
                print(f"    ALL TOP-LEVEL KEYS: {sorted(anc.keys())}", flush=True)
                
                # Dump all simple values
                for k in sorted(anc.keys()):
                    v = anc[k]
                    if isinstance(v, (str, int, float, bool)) and k != "extra":
                        print(f"    {k} = {str(v)[:200]}", flush=True)
                
                # Parse extra (triple nested)
                extra_str = anc.get("extra", "")
                if extra_str:
                    try:
                        outer = json.loads(extra_str)
                        if isinstance(outer, list):
                            for ei, entry in enumerate(outer):
                                if isinstance(entry, dict):
                                    print(f"\n    extra[{ei}] keys: {sorted(entry.keys())}", flush=True)
                                    # Dump simple values in entry
                                    for k in sorted(entry.keys()):
                                        v = entry[k]
                                        if isinstance(v, (str, int, float, bool)) and k != "extra":
                                            print(f"      {k} = {str(v)[:200]}", flush=True)
                                    
                                    # Parse inner extra (product data)
                                    inner_str = entry.get("extra", "")
                                    if inner_str:
                                        try:
                                            prod = json.loads(inner_str)
                                            print(f"\n    PRODUCT DATA:", flush=True)
                                            print(f"      ALL KEYS: {sorted(prod.keys())}", flush=True)
                                            
                                            # Dump EVERY field
                                            for k in sorted(prod.keys()):
                                                v = prod[k]
                                                if isinstance(v, (str, int, float, bool)):
                                                    print(f"      {k} = {v}", flush=True)
                                                elif isinstance(v, dict):
                                                    print(f"      {k} = {json.dumps(v, ensure_ascii=False)[:200]}", flush=True)
                                                elif isinstance(v, list):
                                                    print(f"      {k} = [{len(v)} items]", flush=True)
                                                    if len(v) > 0 and isinstance(v[0], dict):
                                                        # Dump first item
                                                        print(f"        [0] keys: {sorted(v[0].keys())}", flush=True)
                                                        for sk in sorted(v[0].keys()):
                                                            sv = v[0][sk]
                                                            if isinstance(sv, (str, int, float, bool)):
                                                                print(f"        [0].{sk} = {sv}", flush=True)
                                        except json.JSONDecodeError:
                                            print(f"      inner extra parse error", flush=True)
                    except json.JSONDecodeError:
                        print(f"    extra parse error", flush=True)
                
                # Check logExtra
                log_extra = anc.get("logExtra", "")
                if log_extra:
                    try:
                        le = json.loads(log_extra) if isinstance(log_extra, str) else log_extra
                        print(f"\n    logExtra keys: {sorted(le.keys()) if isinstance(le, dict) else type(le)}", flush=True)
                        if isinstance(le, dict):
                            for k in sorted(le.keys()):
                                v = le[k]
                                if isinstance(v, (str, int, float, bool)):
                                    print(f"      {k} = {str(v)[:100]}", flush=True)
                    except:
                        print(f"    logExtra: {str(log_extra)[:200]}", flush=True)

        # Now try to make authenticated API calls using browser cookies
        print("\n\n🔐 Trying authenticated TikTok API calls...", flush=True)
        cookies = await ctx.cookies()
        cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
        print(f"  Cookies: {len(cookies)} cookies", flush=True)
        
        # Try product detail API with cookies
        product_ids = ["1732773678384055322"]  # Our known product
        for pid in product_ids:
            api_url = f"https://www.tiktok.com/api/product/detail/?product_id={pid}"
            print(f"\n  Trying: {api_url}", flush=True)
            try:
                resp = await page.evaluate(f"""async () => {{
                    try {{
                        const r = await fetch("{api_url}", {{
                            headers: {{'Accept': 'application/json'}},
                            credentials: 'include',
                        }});
                        const text = await r.text();
                        return {{status: r.status, body: text.substring(0, 5000)}};
                    }} catch(e) {{
                        return {{error: e.message}};
                    }}
                }}""")
                print(f"  Result: {json.dumps(resp, indent=2)[:2000]}", flush=True)
            except Exception as e:
                print(f"  Error: {e}", flush=True)
        
        # Try OEC API with cookies
        for pid in product_ids:
            api_url = f"https://www.tiktok.com/api/oec/product/detail/?product_id={pid}&region=ID"
            print(f"\n  Trying: {api_url}", flush=True)
            try:
                resp = await page.evaluate(f"""async () => {{
                    try {{
                        const r = await fetch("{api_url}", {{
                            headers: {{'Accept': 'application/json'}},
                            credentials: 'include',
                        }});
                        const text = await r.text();
                        return {{status: r.status, body: text.substring(0, 5000)}};
                    }} catch(e) {{
                        return {{error: e.message}};
                    }}
                }}""")
                print(f"  Result: {json.dumps(resp, indent=2)[:2000]}", flush=True)
            except Exception as e:
                print(f"  Error: {e}", flush=True)

        # Try commerce/anchor detail API
        for pid in product_ids:
            api_url = f"https://www.tiktok.com/api/commerce/anchor/detail/?product_id={pid}"
            print(f"\n  Trying: {api_url}", flush=True)
            try:
                resp = await page.evaluate(f"""async () => {{
                    try {{
                        const r = await fetch("{api_url}", {{
                            headers: {{'Accept': 'application/json'}},
                            credentials: 'include',
                        }});
                        const text = await r.text();
                        return {{status: r.status, body: text.substring(0, 5000)}};
                    }} catch(e) {{
                        return {{error: e.message}};
                    }}
                }}""")
                print(f"  Result: {json.dumps(resp, indent=2)[:2000]}", flush=True)
            except Exception as e:
                print(f"  Error: {e}", flush=True)

        # Summary
        print(f"\n\n{'='*60}", flush=True)
        print(f"📊 Summary:", flush=True)
        print(f"  JSON responses: {len(all_json_responses)}", flush=True)
        print(f"  Price hits: {len(responses_with_price)}", flush=True)
        for h in responses_with_price:
            print(f"    #{h['i']}: {h['url'][:80]}", flush=True)

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
