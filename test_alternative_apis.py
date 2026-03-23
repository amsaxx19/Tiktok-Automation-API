import httpx
from bs4 import BeautifulSoup
import json

def test_tikwm():
    url = "https://www.tikwm.com/api/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json, text/javascript, */*; q=0.01",
    }
    params = {
        "url": "https://www.tiktok.com/@streetorvintage/video/7376307130833636616",
        "hd": 1
    }
    try:
        r = httpx.post(url, data=params, headers=headers, timeout=10)
        print("Tikwm Status:", r.status_code)
        data = r.json()
        print("Tikwm Data keys:", data.keys() if isinstance(data, dict) else type(data))
        if data.get("data"):
            print("Tikwm Data:", list(data["data"].keys()))
            print("Title:", data["data"].get("title"))
    except Exception as e:
        print("Tikwm error:", e)

def test_urlebird():
    url = "https://urlebird.com/video/7376307130833636616/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    }
    try:
        r = httpx.get(url, headers=headers, follow_redirects=True, timeout=10)
        print("Urlebird Status:", r.status_code, r.url)
        soup = BeautifulSoup(r.text, 'html.parser')
        print("Urlebird text length:", len(r.text))
        print("Has 'quitting a job'?", "quitting a job" in r.text.lower())
    except Exception as e:
        print("Urlebird error:", e)

if __name__ == "__main__":
    test_tikwm()
    test_urlebird()
