import requests

BASE_URL = "http://localhost:8000"
TIMEOUT = 30

def test_get_api_search_keyword_search():
    url = f"{BASE_URL}/api/search"
    params = {
        "keyword": "viral dance",
        "platforms": "tiktok",
        "max_results": 3,
        "sort": "views"
    }
    headers = {
        "Accept": "application/json"
    }
    try:
        response = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
    except requests.RequestException as e:
        assert False, f"Request failed: {e}"

    if response.status_code == 200:
        json_data = {}
        try:
            json_data = response.json()
        except Exception:
            assert False, "Response is not valid JSON"
        assert isinstance(json_data, dict), "Response JSON is not a dictionary"
        assert "results" in json_data, "'results' key missing in response"
        assert isinstance(json_data["results"], list), "'results' is not a list"
        assert "json_file" in json_data, "'json_file' key missing in response"
        assert isinstance(json_data["json_file"], str), "'json_file' is not a string"
        assert "csv_file" in json_data, "'csv_file' key missing in response"
        assert isinstance(json_data["csv_file"], str), "'csv_file' is not a string"
    elif response.status_code == 429:
        # TikTok rate-limit case for the cloud runner is acceptable
        json_data = {}
        try:
            json_data = response.json()
        except Exception:
            assert False, "Response 429 is not valid JSON"
        assert "error" in json_data or "msg" in json_data, "429 response missing error message"
    else:
        assert False, f"Unexpected status code: {response.status_code} with body: {response.text}"

test_get_api_search_keyword_search()