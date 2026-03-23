import asyncio
import json
from scrapling.fetchers import AsyncStealthySession

async def main():
    async with AsyncStealthySession(headless=True) as session:
        url = "https://www.tiktok.com/@streetorvintage/video/7376307130833636616"
        print(f"Fetching {url}")
        # Trying network_idle to let TikTok fully hydrate
        resp = await session.fetch(url, network_idle=True, timeout=20000)
        
        # Check if actual subtitle DOM elements exist instead of just the JSON
        subtitle_els = resp.css('div[class*="Subtitle"], div[data-e2e="video-subtitle"]')
        if subtitle_els:
            print(f"Found subtitle DOM elements: {len(subtitle_els)}")
            for el in subtitle_els:
                print(el.text)
        
        scripts = resp.css('script#__UNIVERSAL_DATA_FOR_REHYDRATION__')
        if scripts:
            data = json.loads(scripts[0].text)
            print("StatusMsg:", data.get("__DEFAULT_SCOPE__", {}).get("webapp.video-detail", {}).get("statusMsg"))
            item = data.get("__DEFAULT_SCOPE__", {}).get("webapp.video-detail", {}).get("itemInfo", {}).get("itemStruct", {})
            if item:
                print("itemStruct object found with keys:", list(item.keys()))
                if "contents" in item:
                    print("Contents length:", len(item["contents"]))
                    print("First content:", item["contents"][0] if item["contents"] else "Empty")
            else:
                print("itemStruct is empty")
        else:
            print("No Universal Data script found")

if __name__ == "__main__":
    asyncio.run(main())
