import httpx
import json

def test_mobile_api():
    video_id = "7376307130833636616"
    url = f"https://api16-normal-c-useast1a.tiktokv.com/aweme/v1/feed/?aweme_id={video_id}"
    headers = {
        "User-Agent": "TikTok 26.2.0 rv:262018 (iPhone; iOS 14.4.2; en_US) Cronet",
    }
    
    try:
        r = httpx.get(url, headers=headers, timeout=15)
        print("Mobile API Status:", r.status_code)
        data = r.json()
        with open("mobile_api_test.json", "w") as f:
            json.dump(data, f, indent=2)
            
        aweme_list = data.get("aweme_list", [])
        if aweme_list:
            video_data = aweme_list[0]
            print("Successfully extracted video from mobile API!")
            print("Description:", video_data.get("desc"))
            
            # Check for subtitles/transcripts
            video_info = video_data.get("video", {})
            subtitles = video_info.get("subtitle_infos", [])
            print("Subtitles found:", len(subtitles))
        else:
            print("No aweme_list found in response.")
            print("Keys:", list(data.keys()))
            
    except Exception as e:
        print("Mobile API error:", e)

if __name__ == "__main__":
    test_mobile_api()
