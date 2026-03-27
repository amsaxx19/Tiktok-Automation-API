import requests

BASE_URL = "http://localhost:8000"
TIMEOUT = 30

def test_post_auth_signin_user_authentication():
    url = f"{BASE_URL}/api/auth/signin"
    # For the test, we need some email and password.
    # Since the test case mentions behavior on new accounts (email not confirmed),
    # it is acceptable to test with a known test user.
    # Here we try a test email likely existing or unconfirmed. Adjust as necessary.
    test_credentials = {
        "email": "testuser@example.com",
        "password": "TestPass123!"
    }

    try:
        response = requests.post(url, json=test_credentials, timeout=TIMEOUT)
    except requests.RequestException as e:
        assert False, f"Request failed: {e}"

    assert response.headers.get("Content-Type", "").startswith("application/json"), "Response is not JSON"

    try:
        resp_json = response.json()
    except ValueError:
        assert False, "Response content is not valid JSON"

    status = response.status_code

    # Accept 200 or 400 only as per instruction
    assert status in [200, 400], f"Expected status 200 or 400, got {status}"

    if status == 200:
        # Verify 'access_token' and 'user' fields are present
        assert "access_token" in resp_json, "'access_token' not found in response"
        assert "user" in resp_json, "'user' not found in response"
        # access_token should be a non-empty string
        assert isinstance(resp_json["access_token"], str) and resp_json["access_token"], "'access_token' is empty or not a string"
        # user should be a dict/object
        assert isinstance(resp_json["user"], dict), "'user' is not an object"
    elif status == 400:
        # Verify 'error' or 'msg' field is present
        assert "error" in resp_json or "msg" in resp_json, "Neither 'error' nor 'msg' found in 400 response"

test_post_auth_signin_user_authentication()