"""
Wait 25 minutes for Kalodata rate limit to expire, then test API login.

The rate limit returns "22:48" on every attempt and resets the timer,
so we must wait WITHOUT any login attempts for the full duration.

Usage:
    nohup .venv/bin/python3 -u scripts/wait_and_login.py > /tmp/kalodata_wait.log 2>&1 &
    tail -f /tmp/kalodata_wait.log
"""

import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timedelta

import httpx

WAIT_MINUTES = 25
EMAIL = "amosthiosa1999@gmail.com"
PASSWORD = "Amoskeren_90"
PRODUCT_ID = "1732181709510707083"


async def main():
    target = datetime.now() + timedelta(minutes=WAIT_MINUTES)
    print(
        f"Waiting until {target.strftime('%H:%M:%S')} "
        f"(no login attempts)...",
        flush=True,
    )

    # Wait with progress
    for i in range(WAIT_MINUTES):
        await asyncio.sleep(60)
        remaining = WAIT_MINUTES - i - 1
        if remaining % 5 == 0 or remaining <= 2:
            print(f"  ... {remaining} minutes remaining", flush=True)

    print(
        f"\nAttempting login at {datetime.now().strftime('%H:%M:%S')}...",
        flush=True,
    )

    device_id = hashlib.md5(str(uuid.uuid4()).encode()).hexdigest()
    jar = httpx.Cookies()
    jar.set("deviceId", device_id, domain="www.kalodata.com")
    jar.set("appVersion", "2.0", domain="www.kalodata.com")
    jar.set("deviceType", "pc", domain="www.kalodata.com")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": "https://www.kalodata.com",
        "language": "en-US",
        "country": "ID",
        "currency": "IDR",
    }

    async with httpx.AsyncClient(
        timeout=20, follow_redirects=True, cookies=jar
    ) as client:
        # Get SESSION cookie
        await client.get(
            "https://www.kalodata.com/",
            headers={"User-Agent": headers["User-Agent"]},
        )

        # Attempt login
        lr = await client.post(
            "https://www.kalodata.com/user/login",
            json={
                "scene": "login",
                "loginMethod": "EMAIL_PASSWORD",
                "tcCode": "",
                "email": EMAIL,
                "emailPassword": PASSWORD,
            },
            headers=headers,
        )
        data = lr.json()

        if data.get("success"):
            print("✅ LOGIN SUCCESS!", flush=True)

            # Register access
            await client.post(
                "https://www.kalodata.com/product/detail/access",
                json={"id": PRODUCT_ID},
                headers=headers,
            )

            # Product detail
            resp = await client.post(
                "https://www.kalodata.com/product/detail",
                json={"id": PRODUCT_ID},
                headers=headers,
            )
            dj = resp.json()
            if dj.get("success"):
                detail = dj.get("data", {})
                print(f"\n📦 Product Detail:", flush=True)
                for key in [
                    "product_title",
                    "seller_type",
                    "min_original_price",
                    "unit_price",
                    "is_tokopedia",
                    "brand_name",
                    "delivery_type",
                    "shop_rating",
                    "review_count",
                    "ter_cate_id",
                    "sec_cate_id",
                ]:
                    print(f"  {key}: {detail.get(key, 'N/A')}", flush=True)
            else:
                print(f"❌ detail failed: {json.dumps(dj)}", flush=True)

            # Total stats (last 30 days)
            end = datetime.now().strftime("%Y-%m-%d")
            start = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

            tr = await client.post(
                "https://www.kalodata.com/product/detail/total",
                json={
                    "id": PRODUCT_ID,
                    "startDate": start,
                    "endDate": end,
                },
                headers=headers,
            )
            tj = tr.json()
            if tj.get("success"):
                total = tj.get("data", {})
                print(f"\n📊 Total Stats (last 30 days):", flush=True)
                print(f"  Full response: {json.dumps(total, indent=2)}", flush=True)
            else:
                print(f"❌ total failed: {json.dumps(tj)}", flush=True)

            # History (last 30 days)
            hr = await client.post(
                "https://www.kalodata.com/product/detail/history",
                json={
                    "id": PRODUCT_ID,
                    "startDate": start,
                    "endDate": end,
                },
                headers=headers,
            )
            hj = hr.json()
            if hj.get("success"):
                hist = hj.get("data", [])
                print(f"\n📈 History: {len(hist)} data points", flush=True)
                if hist:
                    for item in hist[-5:]:
                        print(f"  {json.dumps(item)}", flush=True)
            else:
                print(f"❌ history failed: {json.dumps(hj)}", flush=True)

            print("\n✅ ALL TESTS COMPLETE", flush=True)
        else:
            print(f"❌ Still rate-limited: {json.dumps(data)}", flush=True)
            print(
                "Suggestion: wait longer or try a different account",
                flush=True,
            )


if __name__ == "__main__":
    asyncio.run(main())
