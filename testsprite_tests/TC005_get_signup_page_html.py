import requests

BASE_URL = "http://localhost:8000"

def test_get_signup_page_html():
    url = f"{BASE_URL}/signup"
    headers = {
        "Accept": "text/html"
    }
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        assert response.status_code == 200, f"Expected status code 200, got {response.status_code}"
        content_type = response.headers.get("Content-Type", "")
        assert "text/html" in content_type, f"Expected 'text/html' in Content-Type, got {content_type}"
    except requests.RequestException as e:
        assert False, f"Request failed: {e}"

test_get_signup_page_html()