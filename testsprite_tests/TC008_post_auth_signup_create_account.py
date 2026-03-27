import requests
import uuid

BASE_URL = "http://localhost:8000"
TIMEOUT = 30

def test_post_auth_signup_create_account():
    url = f"{BASE_URL}/api/auth/signup"
    unique_email = f"testuser_{uuid.uuid4().hex}@gmail.com"
    payload = {
        "email": unique_email,
        "password": "ValidPass123!",
        "use_case": "testing"
    }
    headers = {
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=TIMEOUT)
    except requests.RequestException as e:
        assert False, f"Request to {url} failed: {e}"

    assert response.status_code in (200, 201, 429), f"Unexpected status code {response.status_code}"

    try:
        json_resp = response.json()
    except ValueError:
        assert False, "Response is not in JSON format"

    if response.status_code in (200, 201):
        assert ("message" in json_resp) or ("msg" in json_resp), "Response should contain 'message' or 'msg'"
    elif response.status_code == 429:
        assert ("error_code" in json_resp) or ("msg" in json_resp) or ("error" in json_resp), \
            "429 response should contain 'error_code', 'msg', or 'error'"
    # Do not fail on 429

test_post_auth_signup_create_account()