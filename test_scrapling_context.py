import asyncio
import os
import json
from scrapling.fetchers import AsyncStealthySession

os.environ["TIKTOK_COOKIE"] = "dummy_cookie_value"

async def main():
    async with AsyncStealthySession(headless=True) as session:
        cookie_val = os.environ.get("TIKTOK_COOKIE")
        print("Checking session properties...")
        print(dir(session))
        
        # Test if we can initialize page manually to inject cookie
        page = await session._get_page()
        print("Page:", page)
        if cookie_val:
            await page.context.add_cookies([{
                "name": "sessionid",
                "value": cookie_val,
                "domain": ".tiktok.com",
                "path": "/"
            }])
            print("Successfully injected cookie into context")

if __name__ == "__main__":
    asyncio.run(main())
