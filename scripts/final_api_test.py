"""
Final wait for rate limit expiry at 23:51 Beijing time.
Waits until 23:53 (2 min buffer), then tests the full API pipeline.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone

async def main():
    beijing_tz = timezone(timedelta(hours=8))
    now_bj = datetime.now(beijing_tz)

    # Target: 23:53 Beijing
    target_bj = now_bj.replace(hour=23, minute=53, second=0, microsecond=0)
    if target_bj < now_bj:
        target_bj += timedelta(days=1)

    wait_sec = (target_bj - now_bj).total_seconds()
    print(
        f"Beijing now: {now_bj.strftime('%H:%M:%S')}\n"
        f"Target: 23:53:00 Beijing ({int(wait_sec/60)} min)\n"
        f"Waiting...",
        flush=True,
    )

    if wait_sec > 0:
        await asyncio.sleep(wait_sec)

    print(
        f"\nAttempting at Beijing "
        f"{datetime.now(beijing_tz).strftime('%H:%M:%S')}",
        flush=True,
    )

    # Test via the module
    from scraper.kalodata import KalodataScraper, _login_cooldown_until
    import scraper.kalodata as kmod

    # Reset the cooldown so _ensure_client tries again
    kmod._login_cooldown_until = 0.0

    scraper = KalodataScraper()
    try:
        result = await scraper.get_product("1732181709510707083")
        if result:
            print(f"\n✅ Product via: {result.source}", flush=True)
            print(f"  title: {result.title[:80]}", flush=True)
            print(f"  price_min_idr: Rp{result.price_min_idr:,}", flush=True)
            print(f"  shop_name: {result.shop_name}", flush=True)
            print(f"  seller_type: {result.seller_type}", flush=True)
            print(f"  category: {result.category}", flush=True)
            print(f"  revenue_idr: Rp{result.revenue_idr:,}", flush=True)
            print(f"  items_sold: {result.items_sold:,}", flush=True)
            print(f"  video_revenue: Rp{result.video_revenue_idr:,}", flush=True)
            print(f"  mall_revenue: Rp{result.mall_revenue_idr:,}", flush=True)
            print(f"  creators: {result.related_creator_count}", flush=True)

            if result.source == "kalodata_api":
                print(f"\n🎉 FULL API MODE WORKING!", flush=True)
            else:
                print(f"\n⚠️ Fell back to SSR", flush=True)
        else:
            print("❌ No result", flush=True)
    except Exception as e:
        print(f"❌ Error: {e}", flush=True)
    finally:
        await scraper.close()


if __name__ == "__main__":
    asyncio.run(main())
