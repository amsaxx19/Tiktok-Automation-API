#!/usr/bin/env python3
"""
Use Playwright with stealth to get Tokopedia product price.
Strategy: Navigate to Tokopedia product page, wait for page render,
extract price from DOM or embedded JSON data.
"""
import asyncio, json, re, os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
PROXY = os.getenv("PROXY_URL", "")

# Known product URLs from our scraping
PRODUCTS = [
    {
        "name": "Pembersih Busa Sofa",
        "seo_url": "https://shop-id.tokopedia.com/pdp/pembersih-busa-sofa-kain-500ml-antibakteri-formula-lembut-wangi-tahan-24-jam-penghilang-noda-kuat/1732773678384055322",
        "product_id": "1732773678384055322",
    },
]


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
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        
        # Try different approaches
        approaches = [
            ("Desktop Chrome", {
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "viewport": {"width": 1920, "height": 1080},
                "locale": "id-ID",
            }),
            ("Mobile Chrome", {
                "user_agent": "Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
                "viewport": {"width": 412, "height": 915},
                "locale": "id-ID",
                "is_mobile": True,
                "has_touch": True,
            }),
        ]
        
        for approach_name, ctx_opts in approaches:
            print(f"\n{'='*60}", flush=True)
            print(f"🌐 Approach: {approach_name}", flush=True)
            print(f"{'='*60}", flush=True)
            
            ctx = await browser.new_context(proxy=pw_proxy, **ctx_opts)
            page = await ctx.new_page()
            
            # Intercept responses
            json_responses = []
            
            async def on_response(response):
                url = response.url
                ct = response.headers.get("content-type", "")
                if response.status == 200 and "json" in ct:
                    try:
                        body = await response.text()
                        if len(body) > 100:
                            json_responses.append({"url": url[:150], "body": body, "size": len(body)})
                    except:
                        pass
            
            page.on("response", on_response)
            
            product = PRODUCTS[0]
            url = product["seo_url"]
            
            print(f"  Navigating to: {url[:80]}...", flush=True)
            try:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                print(f"  Response status: {resp.status if resp else 'None'}", flush=True)
            except Exception as e:
                print(f"  Navigation error: {e}", flush=True)
            
            await page.wait_for_timeout(3000)
            final_url = page.url
            title = await page.title()
            print(f"  Final URL: {final_url}", flush=True)
            print(f"  Title: {title}", flush=True)
            
            # Check for security challenge
            content = await page.content()
            if "security" in content.lower() or "verify" in content.lower() or "puzzle" in content.lower():
                print("  🔒 Security check detected", flush=True)
                # Wait longer to see if it auto-resolves
                await page.wait_for_timeout(8000)
                content = await page.content()
                title = await page.title()
                print(f"  After wait - Title: {title}", flush=True)
                
                if "security" in content.lower() or "verify" in content.lower():
                    print("  ❌ Still blocked by security", flush=True)
                    # Screenshot
                    await page.screenshot(path=f"scripts/tokopedia_{approach_name.replace(' ','_').lower()}.png")
                    print(f"  Screenshot saved", flush=True)
                    await ctx.close()
                    continue
            
            # Try to extract price from page
            print("\n  📊 Checking for price data...", flush=True)
            
            # Method 1: Look for Rp in page text
            rp_prices = re.findall(r"Rp\s?[\d.,]+", content)
            if rp_prices:
                print(f"  💰 Rp prices in HTML: {rp_prices[:10]}", flush=True)
            
            # Method 2: LD+JSON
            try:
                ld_data = await page.evaluate("""() => {
                    const scripts = document.querySelectorAll('script[type="application/ld+json"]');
                    return Array.from(scripts).map(s => s.textContent);
                }""")
                for ld in ld_data:
                    if "price" in ld.lower():
                        print(f"  💰 LD+JSON: {ld[:500]}", flush=True)
            except:
                pass
            
            # Method 3: Meta tags
            try:
                metas = await page.evaluate("""() => {
                    const metas = document.querySelectorAll('meta[property*="price"], meta[name*="price"], meta[property*="amount"]');
                    return Array.from(metas).map(m => ({
                        prop: m.getAttribute('property') || m.getAttribute('name'),
                        content: m.getAttribute('content'),
                    }));
                }""")
                if metas:
                    print(f"  💰 Meta tags: {metas}", flush=True)
            except:
                pass
            
            # Method 4: DOM elements with price class
            try:
                price_elements = await page.evaluate("""() => {
                    const results = [];
                    // Try common price selectors
                    const selectors = [
                        '[class*="price"]', '[class*="Price"]',
                        '[data-testid*="price"]', '[data-testid*="Price"]',
                        '.css-175oi2r', // Tokopedia price container
                    ];
                    for (const sel of selectors) {
                        const els = document.querySelectorAll(sel);
                        els.forEach(el => {
                            const text = (el.textContent || '').trim();
                            if (text && text.length < 100) {
                                results.push({sel, text});
                            }
                        });
                    }
                    return results.slice(0, 20);
                }""")
                if price_elements:
                    print(f"  💰 Price elements: {price_elements[:10]}", flush=True)
            except:
                pass
            
            # Method 5: Inline scripts with product data
            try:
                script_data = await page.evaluate("""() => {
                    const scripts = document.querySelectorAll('script');
                    const results = [];
                    scripts.forEach(s => {
                        const text = s.textContent || '';
                        if (text.includes('price') && text.includes('product') && text.length > 200) {
                            // Try to find price patterns
                            const priceMatches = text.match(/"price"\s*:\s*"?(\d+)"?/g);
                            if (priceMatches) {
                                results.push({
                                    preview: text.substring(0, 300),
                                    priceMatches: priceMatches.slice(0, 10),
                                });
                            }
                        }
                    });
                    return results.slice(0, 5);
                }""")
                if script_data:
                    for sd in script_data:
                        print(f"  💰 Script data: prices={sd.get('priceMatches')}", flush=True)
                        print(f"     Preview: {sd.get('preview','')[:200]}", flush=True)
            except:
                pass
            
            # Method 6: Check intercepted JSON responses
            if json_responses:
                print(f"\n  📥 JSON responses captured: {len(json_responses)}", flush=True)
                for jr in json_responses:
                    body = jr["body"]
                    if re.search(r'"price"\s*:\s*"?[1-9]', body) or "Rp" in body:
                        print(f"  💰 JSON with price: {jr['url'][:80]} ({jr['size']} bytes)", flush=True)
                        # Try to extract price
                        prices = re.findall(r'"price"\s*:\s*"?(\d+)"?', body)
                        rps = re.findall(r'Rp\s?[\d.,]+', body)
                        if prices:
                            print(f"     Prices: {prices[:5]}", flush=True)
                        if rps:
                            print(f"     Rp: {rps[:5]}", flush=True)
            
            await ctx.close()
        
        await browser.close()
        print("\n✅ Done", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
