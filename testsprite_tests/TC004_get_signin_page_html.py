import requests

BASE_URL = "http://localhost:8000"
TIMEOUT = 30

def test_get_signin_page_html():
    url = f"{BASE_URL}/signin"
    headers = {
        "Accept": "text/html"
    }
    try:
        response = requests.get(url, headers=headers, timeout=TIMEOUT)
    except requests.RequestException as e:
        assert False, f"Request to {url} failed: {e}"

    assert response.status_code == 200, f"Expected status code 200 but got {response.status_code}"
    content_type = response.headers.get("Content-Type", "")
    assert "text/html" in content_type, f"Expected 'text/html' in Content-Type but got '{content_type}'"

test_get_signin_page_html()