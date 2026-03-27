import requests

BASE_URL = "http://localhost:8000"
TIMEOUT = 30

def test_get_start_page_html():
    url = f"{BASE_URL}/start"
    headers = {
        "Accept": "text/html"
    }
    try:
        response = requests.get(url, headers=headers, timeout=TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as e:
        assert False, f"Request to {url} failed: {e}"

    assert response.status_code == 200, f"Expected status 200 but got {response.status_code}"
    content_type = response.headers.get("Content-Type", "")
    assert "text/html" in content_type, f"Expected 'text/html' Content-Type but got '{content_type}'"
    assert len(response.text) > 0, "Response HTML is empty"

test_get_start_page_html()