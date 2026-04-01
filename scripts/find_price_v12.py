#!/usr/bin/env python3
"""
Focus on OEC ecommerce view which showed Rp6.
Also try Tokopedia with HTTP/1.1 only.
"""
import asyncio, json, re, os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
PROXY = os.getenv("PROXY_URL", "")

PRODUCT_ID = "1732773678384055322"
SELLER_ID = "7494180771761980442"
VIDEO_ID = "7622303845783309575"

OUT_DIR = Path(__file__).resolve().parent / "price_final6"
OUT_DIR.mkdir(exist_ok=True)


async def try_oec_detailed():
    """Load OEC ecommerce view with more parameters."""
    print("=== OEC Ecommerce View (detailed) ===", flush=True)
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
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            proxy=pw_proxy,
            user_agent="Mozilla/5.0 (Linux; Android 14; SM-S928B; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/131.0.0.0 Mobile Safari/537.36 BytedanceWebview/d8a21c6",
            locale="id-ID",
            viewport={"width": 412, "height": 915},
            is_mobile=True,
        )
        page = await ctx.new_page()

        # Build a proper URL with all needed parameters
        base_url = "https://oec-api.tiktokv.com/view/fe_tiktok_ecommerce_upgrade/index.html"
        params = {
            "enter_from": "video",
            "hide_nav_bar": "1",
            "view_background_color_auto_dark": "1",
            "should_full_screen": "1",
            "product_id": PRODUCT_ID,
            "seller_id": SELLER_ID,
            "region": "ID",
            "language": "id",
        }
        from urllib.parse import urlencode
        full_url = f"{base_url}?{urlencode(params)}"
        
        print(f"  URL: {full_url[:120]}...", flush=True)
        try:
            await page.goto(full_url, wait_until="domcontentloaded", timeout=20000)
        except Exception as e:
            print(f"  Nav: {e}", flush=True)
        
        await page.wait_for_timeout(8000)
        
        content = await page.content()
        with open(OUT_DIR / "oec_page.html", "w") as f:
            f.write(content)
        print(f"  Page size: {len(content)} bytes", flush=True)
        
        # Find ALL Rp occurrences
        rp_matches = re.findall(r"Rp\s?[\d.,]+", content)
        print(f"  Rp matches: {rp_matches[:20]}", flush=True)
        
        # Get visible text
        try:
            text = await page.evaluate("() => document.body?.innerText || ''")
            print(f"  Visible text: {text[:500]}", flush=True)
            
            # Check for prices in text
            rp_text = re.findall(r"Rp\s?[\d.,]+", text)
            if rp_text:
                print(f"  💰 Rp in visible text: {rp_text[:10]}", flush=True)
        except:
            pass
        
        # Take screenshot
        await page.screenshot(path=str(OUT_DIR / "oec_screenshot.png"))
        print(f"  Screenshot saved", flush=True)
        
        await browser.close()


async def try_tokopedia_http1():
    """Try Tokopedia with HTTP/1.1 only (disable HTTP/2)."""
    print("\n\n=== Tokopedia with HTTP/1.1 ===", flush=True)
    import httpx
    
    # Use httpx without h2 to force HTTP/1.1
    product_url = "https://www.tokopedia.com/search?q=seumnida+sofa+stain+remover"
    
    try:
        # Note: httpx with http2=False (default) forces HTTP/1.1
        async with httpx.AsyncClient(
            timeout=20, 
            follow_redirects=True,
            proxy=PROXY,
            http2=False,  # Force HTTP/1.1
        ) as c:
            resp = await c.get(product_url, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "id-ID,id;q=0.9",
                "Accept-Encoding": "gzip, deflate",
            })
            print(f"  Status: {resp.status_code}", flush=True)
            print(f"  Protocol: {resp.http_version}", flush=True)
            
            content = resp.text
            if "security" in content.lower()[:500] or "verify" in content.lower()[:500]:
                print("  🔒 Security check", flush=True)
            else:
                rp = re.findall(r"Rp\s?[\d.,]+", content)
                if rp:
                    print(f"  💰 Prices: {rp[:10]}", flush=True)
                else:
                    print(f"  First 300: {content[:300]}", flush=True)
    except Exception as e:
        print(f"  Error: {e}", flush=True)
    
    # Also try www.tokopedia.com homepage
    print("\n  Trying homepage...", flush=True)
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True, proxy=PROXY, http2=False) as c:
            resp = await c.get("https://www.tokopedia.com/", headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
                "Accept": "text/html",
            })
            print(f"  Homepage status: {resp.status_code}", flush=True)
            ct = resp.headers.get("content-type", "")
            print(f"  CT: {ct}", flush=True)
            if resp.status_code == 200:
                title_match = re.search(r"<title>(.*?)</title>", resp.text, re.I)
                if title_match:
                    print(f"  Title: {title_match.group(1)[:80]}", flush=True)
    except Exception as e:
        print(f"  Error: {e}", flush=True)

    # Try m.tokopedia.com (mobile)
    print("\n  Trying m.tokopedia.com...", flush=True)
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True, proxy=PROXY, http2=False) as c:
            resp = await c.get("https://m.tokopedia.com/search?q=seumnida+sofa+stain+remover", headers={
                "User-Agent": "Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 Chrome/131.0.0.0 Mobile Safari/537.36",
                "Accept": "text/html",
                "Accept-Language": "id-ID,id;q=0.9",
            })
            print(f"  Status: {resp.status_code}", flush=True)
            if "security" in resp.text.lower()[:500]:
                print("  🔒 Security check", flush=True)
            else:
                rp = re.findall(r"Rp\s?[\d.,]+", resp.text)
                if rp:
                    print(f"  💰 Prices: {rp[:10]}", flush=True)
                else:
                    print(f"  First 300: {resp.text[:300]}", flush=True)
    except Exception as e:
        print(f"  Error: {e}", flush=True)


async def try_google_shopping():
    """Try Google Shopping search to find product with price."""
    print("\n\n=== Google Shopping ===", flush=True)
    import httpx
    
    query = "seumnida sofa stain remover spray tokopedia"
    url = f"https://www.google.com/search?q={query.replace(' ','+')}&tbm=shop&hl=id&gl=id"
    
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True, proxy=PROXY) as c:
            resp = await c.get(url, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
                "Accept": "text/html",
                "Accept-Language": "id-ID,id;q=0.9",
            })
            print(f"  Status: {resp.status_code}", flush=True)
            
            if "captcha" in resp.text.lower() or "unusual" in resp.text.lower():
                print("  🔒 Google blocked", flush=True)
            else:
                rp = re.findall(r"Rp\s?[\d.,]+", resp.text)
                if rp:
                    print(f"  💰 Prices from Google Shopping: {rp[:10]}", flush=True)
                else:
                    print(f"  No Rp prices found", flush=True)
                    # Check for other price formats
                    prices = re.findall(r"\d{2,3}\.\d{3}", resp.text)
                    if prices:
                        print(f"  Number patterns: {prices[:10]}", flush=True)
    except Exception as e:
        print(f"  Error: {e}", flush=True)

    # Also try regular Google search
    print("\n  Regular Google search...", flush=True)
    url2 = f"https://www.google.com/search?q={query.replace(' ','+')}&hl=id&gl=id"
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True, proxy=PROXY) as c:
            resp = await c.get(url2, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
                "Accept": "text/html",
            })
            print(f"  Status: {resp.status_code}", flush=True)
            rp = re.findall(r"Rp\s?[\d.,]+", resp.text)
            if rp:
                print(f"  💰 Prices: {rp[:10]}", flush=True)
            else:
                if "captcha" in resp.text.lower():
                    print("  🔒 CAPTCHA", flush=True)
                else:
                    print(f"  No prices found", flush=True)
    except Exception as e:
        print(f"  Error: {e}", flush=True)


async def main():
    await try_oec_detailed()
    await try_tokopedia_http1()
    await try_google_shopping()
    print("\n✅ All done", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
