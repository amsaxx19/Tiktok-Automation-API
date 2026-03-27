import requests

BASE_URL = "http://localhost:8000"

def test_get_app_page_html():
    url = f"{BASE_URL}/app"
    headers = {
        "Accept": "text/html"
    }
    try:
        response = requests.get(url, headers=headers, timeout=30)
        assert response.status_code == 200, f"Expected status code 200, got {response.status_code}"
        content_type = response.headers.get("Content-Type", "")
        assert "text/html" in content_type, f"Expected 'text/html' in Content-Type header, got {content_type}"
        html_content = response.text
        assert len(html_content) > 0, "Response HTML content is empty"
    except requests.RequestException as e:
        assert False, f"Request to {url} failed with exception: {e}"

test_get_app_page_html()