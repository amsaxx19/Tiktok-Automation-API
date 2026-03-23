import httpx
import json

def test_tikwm():
    url = "https://www.tikwm.com/api/"
    params = {
        "url": "7376307130833636616",
        "hd": 1
    }
    resp = httpx.post(url, data=params, timeout=15)
    print(f"Status: {resp.status_code}")
    data = resp.json()
    with open("tikwm_test.json", "w") as f:
        json.dump(data, f, indent=2)
    print(f"Saved payload length: {len(str(data))}")

if __name__ == "__main__":
    test_tikwm()
