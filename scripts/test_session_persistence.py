"""
Wait for rate limit to expire, then test:
1. Fresh login + session save
2. Close client, create new instance
3. Session restore from disk (no login needed)
4. Full product fetch through the module
"""
import asyncio
import sys
import os
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

async def main():
    beijing_tz = timezone(timedelta(hours=8))
    now_bj = datetime.now(beijing_tz)

    # Target: 01:01 Beijing (00:57 rate limit + 4 min buffer)
    target_bj = now_bj.replace(hour=1, minute=1, second=0, microsecond=0)
    if target_bj < now_bj:
        target_bj += timedelta(days=1)

    wait_sec = (target_bj - now_bj).total_seconds()
    print(
        f"Beijing now: {now_bj.strftime('%H:%M:%S')}\n"
        f"Target: 01:01:00 Beijing ({int(wait_sec/60)} min)\n"
        f"Waiting...",
        flush=True,
    )

    if wait_sec > 0:
        await asyncio.sleep(wait_sec)

    print(
        f"\n=== Attempt at Beijing "
        f"{datetime.now(beijing_tz).strftime('%H:%M:%S')} ===",
        flush=True,
    )

    import scraper.kalodata as kmod
    from scraper.kalodata import KalodataScraper, _SESSION_FILE

    # Reset cooldown
    kmod._login_cooldown_until = 0.0

    # Delete old session file
    if _SESSION_FILE.exists():
        _SESSION_FILE.unlink()
        print("Deleted old session file", flush=True)

    # === Test 1: Fresh login ===
    print("\n--- Test 1: Fresh API login ---", flush=True)
    s1 = KalodataScraper()
    try:
        result1 = await s1.get_product("1732181709510707083")
        if result1 and result1.source == "kalodata_api":
            print(f"✅ Fresh login works! source={result1.source}", flush=True)
            print(f"   title: {result1.title[:60]}", flush=True)
            print(f"   price_min: Rp{result1.price_min_idr:,}", flush=True)
            print(f"   price_max: Rp{result1.price_max_idr:,}", flush=True)
            print(f"   lowest_30d: Rp{result1.lowest_price_30d_idr:,}", flush=True)
            print(f"   revenue: Rp{result1.revenue_idr:,}", flush=True)
            print(f"   items_sold: {result1.items_sold:,}", flush=True)
            print(f"   shop: {result1.shop_name}", flush=True)
            print(f"   category: {result1.category}", flush=True)
            print(f"   commission: {result1.commission_rate}%", flush=True)
            print(f"   rating: {result1.rating} ({result1.review_count} reviews)", flush=True)
        else:
            src = result1.source if result1 else "None"
            print(f"⚠️ Got result but source={src}", flush=True)
    except Exception as e:
        print(f"❌ Test 1 failed: {e}", flush=True)
        return
    finally:
        await s1.close()

    # Check session file was saved
    if _SESSION_FILE.exists():
        print(f"\n✅ Session saved to {_SESSION_FILE}", flush=True)
    else:
        print(f"\n❌ Session file NOT saved", flush=True)
        return

    # === Test 2: Session restore (no login) ===
    print("\n--- Test 2: Session restore (new instance, no login) ---", flush=True)
    s2 = KalodataScraper()
    try:
        result2 = await s2.get_product("1732181709510707083")
        if result2 and result2.source == "kalodata_api":
            print(f"✅ Session restore works! source={result2.source}", flush=True)
            print(f"   revenue: Rp{result2.revenue_idr:,}", flush=True)
        else:
            src = result2.source if result2 else "None"
            print(f"⚠️ Got result but source={src}", flush=True)
    except Exception as e:
        print(f"❌ Test 2 failed: {e}", flush=True)
    finally:
        await s2.close()

    # === Test 3: Different product ===
    print("\n--- Test 3: Different product ---", flush=True)
    s3 = KalodataScraper()
    try:
        # Try a different product ID (common TikTok shop product)
        result3 = await s3.get_product("1729000000000000000")
        if result3:
            print(f"✅ Different product: source={result3.source}", flush=True)
            print(f"   title: {result3.title[:60] if result3.title else 'N/A'}", flush=True)
        else:
            print(f"⚠️ No result for different product (may not exist)", flush=True)
    except Exception as e:
        print(f"⚠️ Different product: {e}", flush=True)
    finally:
        await s3.close()

    print("\n🎉 ALL TESTS COMPLETE!", flush=True)

if __name__ == "__main__":
    asyncio.run(main())
