"""
Quick test: rate limit should be expired (past 23:53 Beijing).
Run from project root: .venv/bin/python3 scripts/test_api_now.py
"""
import asyncio
import sys
import os
from datetime import datetime, timedelta, timezone

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

async def main():
    beijing_tz = timezone(timedelta(hours=8))
    now_bj = datetime.now(beijing_tz)
    print(f"Beijing time: {now_bj.strftime('%H:%M:%S')}", flush=True)

    import scraper.kalodata as kmod
    from scraper.kalodata import KalodataScraper

    # Reset the cooldown so _ensure_client tries fresh
    kmod._login_cooldown_until = 0.0

    scraper = KalodataScraper()
    try:
        result = await scraper.get_product("1732181709510707083")
        if result:
            print(f"\n✅ Product via: {result.source}", flush=True)
            print(f"  title: {result.title[:80]}", flush=True)
            print(f"  price_min_idr: Rp{result.price_min_idr:,}", flush=True)
            print(f"  price_max_idr: Rp{result.price_max_idr:,}", flush=True)
            print(f"  shop_name: {result.shop_name}", flush=True)
            print(f"  seller_type: {result.seller_type}", flush=True)
            print(f"  ship_from: {result.ship_from}", flush=True)
            print(f"  category: {result.category}", flush=True)
            print(f"  commission_rate: {result.commission_rate}", flush=True)
            print(f"  rating: {result.rating}", flush=True)
            print(f"  review_count: {result.review_count}", flush=True)
            print(f"  revenue_idr: Rp{result.revenue_idr:,}", flush=True)
            print(f"  items_sold: {result.items_sold:,}", flush=True)
            print(f"  avg_unit_price_idr: Rp{result.avg_unit_price_idr:,}", flush=True)
            print(f"  video_revenue: Rp{result.video_revenue_idr:,}", flush=True)
            print(f"  live_revenue: Rp{result.live_revenue_idr:,}", flush=True)
            print(f"  mall_revenue: Rp{result.mall_revenue_idr:,}", flush=True)
            print(f"  creators: {result.related_creator_count}", flush=True)
            print(f"  is_tokopedia: {result.is_tokopedia}", flush=True)

            if result.source == "kalodata_api":
                print(f"\n🎉 FULL API MODE WORKING!", flush=True)
            else:
                print(f"\n⚠️ Fell back to SSR", flush=True)
        else:
            print("❌ No result returned", flush=True)
    except Exception as e:
        import traceback
        print(f"❌ Error: {e}", flush=True)
        traceback.print_exc()
    finally:
        await scraper.close()

if __name__ == "__main__":
    asyncio.run(main())
