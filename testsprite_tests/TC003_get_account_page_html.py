import requests

BASE_URL = "http://localhost:8000"
TIMEOUT = 30

def test_get_account_page_html():
    url = f"{BASE_URL}/account"
    headers = {
        "Accept": "text/html"
    }
    try:
        response = requests.get(url, headers=headers, timeout=TIMEOUT)
        assert response.status_code == 200, f"Expected status 200, got {response.status_code}"
        content_type = response.headers.get("Content-Type", "")
        assert "text/html" in content_type, f"Expected 'text/html' content type, got {content_type}"
        assert response.text.strip() != "", "Response HTML content is empty"
    except requests.RequestException as e:
        assert False, f"Request to {url} failed: {e}"

test_get_account_page_html()