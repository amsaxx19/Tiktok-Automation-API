from scrapling import StealthyFetcher
import json

def main():
    fetcher = StealthyFetcher(headless=True)
    url = "https://www.tiktok.com/@dr.kamilah/video/7339832810842574088"
    print(f"Fetching {url}")
    # Use sync fetcher identically to how it was used "kemaren"
    resp = fetcher.fetch(url, wait_selector='script#__UNIVERSAL_DATA_FOR_REHYDRATION__', timeout=20000)
    
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
    main()
