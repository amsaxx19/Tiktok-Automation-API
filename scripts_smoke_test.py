#!/usr/bin/env python3
"""Basic smoke test for Playground core routes.
Run with: .venv/bin/python scripts_smoke_test.py
"""

from fastapi.testclient import TestClient
import server

client = TestClient(server.app)

TESTS = [
    ("landing", "/", 200),
    ("billing_plans", "/api/billing/plans", 200),
    ("system_config", "/api/system/config", 200),
    ("search_tiktok_alias", "/api/search?keyword=openai&platforms=tiktok&max_results=1", 200),
    ("profile_tiktok_alias", "/api/profile?username=openai&max_results=1", 200),
    ("comments_tiktok_alias", "/api/comments?video_url=https://www.tiktok.com/@openai/video/7604654293966146829&max_comments=3", 200),
]


def main():
    failures = []
    for name, path, expected in TESTS:
        resp = client.get(path)
        ok = resp.status_code == expected
        print(f"[{ 'OK' if ok else 'FAIL' }] {name}: {resp.status_code} {path}")
        if not ok:
            failures.append((name, resp.status_code, path, resp.text[:500]))

    if failures:
        print("\nFailures:")
        for name, status, path, body in failures:
            print(f"- {name} ({status}) {path}\n  {body}\n")
        raise SystemExit(1)

    print("\nAll smoke tests passed.")


if __name__ == "__main__":
    main()
