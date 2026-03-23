import asyncio
import json
from scrapling.fetchers import AsyncStealthySession

async def main():
    async with AsyncStealthySession(headless=False) as session:
        url = "https://www.tiktok.com/@streetorvintage/video/7376307130833636616"
        print(f"Fetching {url} with headless=False")
        resp = await session.fetch(url, wait_selector='script#__UNIVERSAL_DATA_FOR_REHYDRATION__', timeout=20000)
        scripts = resp.css('script#__UNIVERSAL_DATA_FOR_REHYDRATION__')
        if scripts:
            data = json.loads(scripts[0].text)
            print("StatusMsg:", data.get("__DEFAULT_SCOPE__", {}).get("webapp.video-detail", {}).get("statusMsg"))
            item = data.get("__DEFAULT_SCOPE__", {}).get("webapp.video-detail", {}).get("itemInfo", {}).get("itemStruct", {})
            if item:
                print("itemStruct length:", len(str(item)))
                print("item contents:", bool(item.get("contents")))
            else:
                print("itemStruct is empty")
        else:
            print("No Universal Data script found")

if __name__ == "__main__":
    asyncio.run(main())
