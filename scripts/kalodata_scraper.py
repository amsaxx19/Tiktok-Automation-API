"""
Kalodata product data scraper via Playwright browser automation.
Login → navigate to product detail → capture API responses.
"""
import asyncio
import json
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

async def scrape_kalodata_product(product_id: str, email: str, password: str):
    from playwright.async_api import async_playwright
    
    captured = {}
    api_responses = []
    
    async def on_response(response):
        url = response.url
        # Capture all API calls from kalodata
        if "/api/" in url and response.status == 200:
            try:
                ct = response.headers.get("content-type", "")
                if "json" in ct or "application/json" in ct:
                    body = await response.json()
                    api_responses.append({
                        "url": url.split("?")[0],
                        "full_url": url[:300],
                        "status": response.status,
                        "body_keys": list(body.keys()) if isinstance(body, dict) else type(body).__name__,
                        "body_preview": json.dumps(body, ensure_ascii=False)[:500]
                    })
                    # Save full response if it's product-related
                    if "product" in url.lower() or "detail" in url.lower() or "item" in url.lower():
                        captured[url.split("?")[0]] = body
                        print(f"  📦 Captured: {url[:120]}", flush=True)
            except:
                pass
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        )
        page = await context.new_page()
        page.on("response", on_response)
        
        # Step 1: Go to Kalodata login page
        print("🔐 Step 1: Navigating to Kalodata login...", flush=True)
        await page.goto("https://www.kalodata.com/login", wait_until="networkidle", timeout=30000)
        await asyncio.sleep(2)
        
        # Check what's on the page
        title = await page.title()
        print(f"  Page title: {title}", flush=True)
        
        # Take screenshot for debugging
        out_dir = Path(__file__).parent / "kalodata_captures"
        out_dir.mkdir(exist_ok=True)
        await page.screenshot(path=str(out_dir / "01_login_page.png"))
        
        # Try to find login form
        # Kalodata typically has email + password fields
        email_input = await page.query_selector('input[type="email"], input[name="email"], input[placeholder*="email" i], input[placeholder*="Email"]')
        pwd_input = await page.query_selector('input[type="password"], input[name="password"]')
        
        if not email_input:
            # Try broader selectors
            inputs = await page.query_selector_all('input')
            print(f"  Found {len(inputs)} input elements", flush=True)
            for i, inp in enumerate(inputs):
                inp_type = await inp.get_attribute("type") or ""
                inp_name = await inp.get_attribute("name") or ""
                inp_ph = await inp.get_attribute("placeholder") or ""
                print(f"    input[{i}]: type={inp_type}, name={inp_name}, placeholder={inp_ph}", flush=True)
                if inp_type in ("text", "email", "") and not email_input:
                    email_input = inp
                if inp_type == "password":
                    pwd_input = inp
        
        if email_input and pwd_input:
            print(f"  ✅ Found login form, filling credentials...", flush=True)
            await email_input.fill(email)
            await pwd_input.fill(password)
            await asyncio.sleep(0.5)
            
            # Look for login button
            btn = await page.query_selector('button[type="submit"], button:has-text("Log in"), button:has-text("Sign in"), button:has-text("Login")')
            if btn:
                print("  🖱️ Clicking login button...", flush=True)
                await btn.click()
                await asyncio.sleep(3)
                await page.wait_for_load_state("networkidle", timeout=15000)
            else:
                # Try pressing Enter
                print("  ⌨️ Pressing Enter to submit...", flush=True)
                await pwd_input.press("Enter")
                await asyncio.sleep(3)
                await page.wait_for_load_state("networkidle", timeout=15000)
            
            await page.screenshot(path=str(out_dir / "02_after_login.png"))
            current_url = page.url
            print(f"  After login URL: {current_url}", flush=True)
            
            # Check if we're logged in
            cookies = await context.cookies()
            auth_cookies = [c for c in cookies if any(k in c["name"].lower() for k in ["token", "auth", "session", "jwt"])]
            print(f"  Auth cookies: {[c['name'] for c in auth_cookies]}", flush=True)
        else:
            print("  ❌ Could not find login form", flush=True)
            # Dump page HTML for debugging
            html = await page.content()
            (out_dir / "login_page.html").write_text(html[:10000])
            print(f"  Saved HTML to login_page.html ({len(html)} bytes)", flush=True)
        
        # Step 2: Navigate to product detail
        product_url = f"https://www.kalodata.com/product/detail?id={product_id}&language=en-US&currency=IDR&region=ID"
        print(f"\n📊 Step 2: Navigating to product page...", flush=True)
        print(f"  URL: {product_url}", flush=True)
        
        await page.goto(product_url, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(3)
        
        await page.screenshot(path=str(out_dir / "03_product_page.png"))
        title = await page.title()
        print(f"  Page title: {title}", flush=True)
        
        # Wait a bit more for async data
        await asyncio.sleep(5)
        
        # Step 3: Try to extract data from the page DOM directly
        print(f"\n📋 Step 3: Extracting data from page...", flush=True)
        
        # Get all text content with prices
        price_text = await page.evaluate("""() => {
            const results = [];
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
            while(walker.nextNode()) {
                const text = walker.currentNode.textContent.trim();
                if (text && (text.includes('$') || text.includes('Rp') || text.includes('IDR') || 
                    text.match(/\\d+[.,]\\d+/) || text.includes('sold') || text.includes('terjual') ||
                    text.includes('revenue') || text.includes('commission'))) {
                    results.push(text);
                }
            }
            return results.slice(0, 50);
        }""")
        print(f"  Price/sales text found: {len(price_text)} items", flush=True)
        for t in price_text[:30]:
            print(f"    {t[:120]}", flush=True)
        
        # Get product info cards
        page_data = await page.evaluate("""() => {
            // Try __NEXT_DATA__ or similar
            const scripts = document.querySelectorAll('script');
            for (const s of scripts) {
                const text = s.textContent || '';
                if (text.includes('productDetail') || text.includes('product_detail') || 
                    text.includes('"price"') || text.includes('"revenue"')) {
                    return text.substring(0, 3000);
                }
            }
            return null;
        }""")
        if page_data:
            print(f"  Found inline script with product data: {page_data[:500]}", flush=True)
        
        # Save final screenshot
        await page.screenshot(path=str(out_dir / "04_final.png"), full_page=True)
        
        # Step 4: Dump all captured API responses
        print(f"\n📡 Step 4: API responses captured: {len(api_responses)}", flush=True)
        for i, resp in enumerate(api_responses):
            print(f"  [{i}] {resp['url']} → keys: {resp['body_keys']}", flush=True)
            print(f"       {resp['body_preview'][:200]}", flush=True)
        
        # Save all captured data
        if captured:
            (out_dir / "captured_api.json").write_text(json.dumps(captured, ensure_ascii=False, indent=2))
            print(f"\n✅ Saved {len(captured)} API responses to kalodata_captures/", flush=True)
        
        (out_dir / "all_api_responses.json").write_text(json.dumps(api_responses, ensure_ascii=False, indent=2))
        
        await browser.close()
    
    return captured, api_responses, price_text


if __name__ == "__main__":
    product_id = sys.argv[1] if len(sys.argv) > 1 else "1732181709510707083"
    
    # Get credentials from env or args
    email = os.getenv("KALODATA_EMAIL", "")
    password = os.getenv("KALODATA_PASSWORD", "")
    
    if not email or not password:
        print("⚠️ No KALODATA_EMAIL / KALODATA_PASSWORD in .env", flush=True)
        print("Running without login (limited data)...", flush=True)
    
    captured, api_responses, price_text = asyncio.run(
        scrape_kalodata_product(product_id, email, password)
    )
    
    print(f"\n{'='*60}", flush=True)
    print(f"Summary:", flush=True)
    print(f"  API responses: {len(api_responses)}", flush=True)
    print(f"  Captured product APIs: {len(captured)}", flush=True)
    print(f"  Price/sales text: {len(price_text)}", flush=True)
