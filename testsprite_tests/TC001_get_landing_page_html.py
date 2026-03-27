import requests

def test_get_landing_page_html():
    base_url = "http://localhost:8000"
    url = f"{base_url}/"
    headers = {
        "Accept": "text/html"
    }
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
    except requests.RequestException as e:
        assert False, f"Request failed: {e}"
    
    assert response.status_code == 200, f"Expected status code 200 but got {response.status_code}"
    content_type = response.headers.get("Content-Type", "")
    assert "text/html" in content_type.lower(), f"Expected 'text/html' in Content-Type but got {content_type}"
    assert len(response.text) > 0, "Response HTML content is empty"

test_get_landing_page_html()