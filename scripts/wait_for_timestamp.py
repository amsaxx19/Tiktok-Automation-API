"""
Wait until the Kalodata rate limit expires at the timestamp shown in the error message.

Discovery: The "22:48" in code 1303 is NOT a countdown — it's a Beijing (UTC+8) timestamp
when the lock expires. Each login attempt during the lock MAY extend it.

This script waits until that exact time + 1 min buffer, then attempts login ONCE.
"""

import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

import httpx

EMAIL = "amosthiosa1999@gmail.com"
PASSWORD = "Amoskeren_90"
PRODUCT_ID = "1732181709510707083"


async def main():
    utc_now = datetime.now(timezone.utc)
    beijing_tz = timezone(timedelta(hours=8))
    beijing_now = utc_now.astimezone(beijing_tz)

    # Target: 22:48 Beijing time + 2 min buffer
    target_beijing = beijing_now.replace(
        hour=22, minute=50, second=0, microsecond=0
    )
    if target_beijing < beijing_now:
        target_beijing += timedelta(days=1)

    target_utc = target_beijing.astimezone(timezone.utc)
    wait_seconds = (target_utc - utc_now).total_seconds()

    if wait_seconds <= 0:
        print("Target already passed, trying immediately", flush=True)
    else:
        print(
            f"Beijing now: {beijing_now.strftime('%H:%M:%S')}",
            flush=True,
        )
        print(
            f"Will try at: {target_beijing.strftime('%H:%M:%S')} Beijing "
            f"({int(wait_seconds/60)} min wait)",
            flush=True,
        )
        await asyncio.sleep(wait_seconds)

    print(
        f"\n🔑 Attempting login at Beijing "
        f"{datetime.now(timezone(timedelta(hours=8))).strftime('%H:%M:%S')}...",
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
        await client.get(
            "https://www.kalodata.com/",
            headers={"User-Agent": headers["User-Agent"]},
        )

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
                print("\n📦 Product Detail:", flush=True)
                print(json.dumps(detail, indent=2, ensure_ascii=False), flush=True)
            else:
                print(f"❌ detail failed: {json.dumps(dj)}", flush=True)

            # Total stats
            end = datetime.now().strftime("%Y-%m-%d")
            start = (datetime.now() - timedelta(days=30)).strftime(
                "%Y-%m-%d"
            )

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
                print("\n📊 Total Stats (30 days):", flush=True)
                print(
                    json.dumps(total, indent=2, ensure_ascii=False),
                    flush=True,
                )
            else:
                print(f"❌ total failed: {json.dumps(tj)}", flush=True)

            # History
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
                print(
                    f"❌ history failed: {json.dumps(hj)}", flush=True
                )

            print("\n✅ ALL TESTS COMPLETE", flush=True)
        else:
            code = data.get("code", "")
            msg = data.get("message", "")
            print(f"❌ Login failed: code={code}, message='{msg}'", flush=True)
            if code == "1303":
                print(
                    f"   Still rate-limited. Message '{msg}' is likely "
                    f"the Beijing (UTC+8) time when lock expires.",
                    flush=True,
                )
                now_beijing = datetime.now(
                    timezone(timedelta(hours=8))
                ).strftime("%H:%M:%S")
                print(f"   Current Beijing time: {now_beijing}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
