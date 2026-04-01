"""
Kalodata login + product detail API capture.
Uses Playwright to login, then navigates to product page to capture API data.
"""
import asyncio
import json
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

async def main(product_id, email, password):
    from playwright.async_api import async_playwright
    
    out = Path(__file__).parent / "kalodata_captures"
    out.mkdir(exist_ok=True)
    
    api_calls = []
    full_responses = {}
    
    async def on_response(response):
        url = response.url
        if "kalodata.com/api/" in url:
            try:
                ct = response.headers.get("content-type", "")
                if "json" in ct:
                    body = await response.json()
                    entry = {
                        "url": url,
                        "status": response.status,
                        "body": body
                    }
                    api_calls.append(entry)
                    
                    # Log interesting ones
                    url_path = url.split("kalodata.com")[1].split("?")[0]
                    is_product = any(k in url.lower() for k in ["product", "detail", "item", "shop", "metric", "trend", "revenue", "sold"])
                    prefix = "📦" if is_product else "  "
                    size = len(json.dumps(body))
                    print(f"  {prefix} API [{response.status}]: {url_path} ({size}B)", flush=True)
                    
                    if is_product:
                        full_responses[url_path] = body
            except:
                pass
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"]
        )
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="en-US",
        )
        page = await ctx.new_page()
        page.on("response", on_response)
        
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        """)
        
        # === Step 1: Login ===
        print("🔐 Step 1: Login to Kalodata...", flush=True)
        try:
            await page.goto("https://www.kalodata.com/login", wait_until="domcontentloaded", timeout=25000)
        except:
            pass
        await asyncio.sleep(3)
        
        # Fill email
        email_input = await page.wait_for_selector('#register_email', timeout=10000)
        await email_input.fill(email)
        
        # Fill password
        pwd_input = await page.wait_for_selector('#register_password', timeout=5000)
        await pwd_input.fill(password)
        
        # Check the agree checkbox
        checkbox = await page.query_selector('.ant-checkbox-input')
        if checkbox:
            await checkbox.click()
            await asyncio.sleep(0.3)
        
        # Click login button
        login_btn = await page.query_selector('button:has-text("Log in")')
        if login_btn:
            print("  🖱️ Clicking Login...", flush=True)
            await login_btn.click()
        else:
            await pwd_input.press("Enter")
        
        # Wait for navigation
        await asyncio.sleep(5)
        await page.screenshot(path=str(out / "after_login.png"))
        
        current_url = page.url
        print(f"  After login URL: {current_url}", flush=True)
        
        # Check login status
        body_text = await page.inner_text("body")
        if "Log in" in body_text[:200] and "Sign up" in body_text[:200]:
            print("  ⚠️ Might still be on login page", flush=True)
            print(f"  Body: {body_text[:300]}", flush=True)
        else:
            print("  ✅ Login appears successful!", flush=True)
        
        # Get cookies for future API calls
        cookies = await ctx.cookies()
        auth_cookies = {c["name"]: c["value"] for c in cookies if len(c["value"]) > 20}
        print(f"  Cookies: {list(auth_cookies.keys())}", flush=True)
        
        # Save cookies
        (out / "cookies.json").write_text(json.dumps(cookies, indent=2))
        
        # === Step 2: Navigate to product detail ===
        product_url = f"https://www.kalodata.com/product/detail?id={product_id}&language=en-US&currency=IDR&region=ID"
        print(f"\n📊 Step 2: Product page {product_id}...", flush=True)
        
        api_calls.clear()  # Reset to only capture product-page calls
        
        try:
            await page.goto(product_url, wait_until="domcontentloaded", timeout=25000)
        except:
            pass
        await asyncio.sleep(5)
        
        # Wait for data to load
        await asyncio.sleep(5)
        
        await page.screenshot(path=str(out / "product_logged_in.png"), full_page=True)
        
        # Extract visible data
        body_text = await page.inner_text("body")
        print(f"\n�� Step 3: Page text...", flush=True)
        print(body_text[:2000], flush=True)
        
        # Save everything
        print(f"\n📡 API calls captured: {len(api_calls)}", flush=True)
        for i, call in enumerate(api_calls):
            url_path = call["url"].split("kalodata.com")[1].split("?")[0]
            body = call["body"]
            size = len(json.dumps(body))
            print(f"  [{i}] {url_path} ({size}B)", flush=True)
            # Save each product-related API call
            fname = url_path.replace("/", "_").strip("_")
            (out / f"api_{fname}.json").write_text(json.dumps(body, indent=2, ensure_ascii=False))
        
        (out / "all_product_apis.json").write_text(json.dumps(api_calls, indent=2, ensure_ascii=False))
        
        await browser.close()
    
    return api_calls

if __name__ == "__main__":
    product_id = sys.argv[1] if len(sys.argv) > 1 else "1732181709510707083"
    email = os.getenv("KALODATA_EMAIL", "")
    password = os.getenv("KALODATA_PASSWORD", "")
    
    if not email or not password:
        print("❌ Set KALODATA_EMAIL and KALODATA_PASSWORD in .env first!", flush=True)
        print("   Example: KALODATA_EMAIL=your@email.com", flush=True)
        print("   Example: KALODATA_PASSWORD=yourpassword", flush=True)
        sys.exit(1)
    
    asyncio.run(main(product_id, email, password))
