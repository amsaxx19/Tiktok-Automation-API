"""Full E2E test with a live video that has anchors."""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

# Use a LIVE video with known anchor
VIDEO_URL = "https://www.tiktok.com/@amosthiosa/video/7622536313668979976"
# Also test with a DEAD video to verify error handling
DEAD_VIDEO_URL = "https://www.tiktok.com/@amosthiosa/video/7485069092459463953"

async def main():
    from scraper.tiktok_shop import TikTokShopScraper

    proxy = os.getenv("PROXY_URL")
    print(f"Proxy: {proxy[:30]}..." if proxy else "No proxy")
    print(f"Video: {VIDEO_URL}")
    print()

    scraper = TikTokShopScraper(proxy_url=proxy)
    
    try:
        products = await asyncio.wait_for(
            scraper.scrape_products_from_video(VIDEO_URL),
            timeout=120,
        )
    except asyncio.TimeoutError:
        print("⏰ TIMEOUT after 120s")
        return
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return

    print(f"\n{'='*60}")
    print(f"📊 RESULTS: {len(products)} product(s)")
    print(f"{'='*60}")
    for i, p in enumerate(products):
        print(f"\n  Product [{i}]:")
        print(f"    name:     {p.name[:80]}")
        print(f"    id:       {p.product_id}")
        print(f"    price:    {p.price}")
        print(f"    orig:     {p.original_price}")
        print(f"    url:      {p.product_url[:100]}")
        print(f"    thumb:    {p.thumbnail[:80] if p.thumbnail else 'N/A'}")
        print(f"    category: {p.category}")
        print(f"    sold:     {p.sold_count}")
        print(f"    shop:     {p.shop_name}")
        if hasattr(p, '_seller_id'):
            print(f"    seller:   {p._seller_id}")
        if hasattr(p, '_play_count'):
            print(f"    plays:    {p._play_count}")

    # Test 2: Dead video — should exit quickly, not hang
    print(f"\n\n{'='*60}")
    print(f"🧪 TEST 2: Dead video (should exit quickly)")
    print(f"{'='*60}")
    
    scraper2 = TikTokShopScraper(proxy_url=proxy)
    try:
        dead_products = await asyncio.wait_for(
            scraper2.scrape_products_from_video(DEAD_VIDEO_URL),
            timeout=90,
        )
        print(f"\n📊 Dead video result: {len(dead_products)} product(s)")
        if scraper2._video_unavailable:
            print("✅ Correctly detected video as unavailable")
    except asyncio.TimeoutError:
        print("❌ TIMEOUT — dead video handling needs work")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
