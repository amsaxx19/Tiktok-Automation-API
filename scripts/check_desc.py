"""Quick check: what's the video description from reflow?"""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv; load_dotenv()

async def main():
    from scraper.tiktok_shop import TikTokShopScraper
    s = TikTokShopScraper(proxy_url=os.getenv("PROXY_URL"))
    products = await s.scrape_products_from_video("https://www.tiktok.com/@amosthiosa/video/7622536313668979976")
    print(f"\nVideo desc captured: '{s._last_video_desc}'")
    print(f"Products: {len(products)}")
    for p in products:
        print(f"  {p.name[:50]} | price={p.price}")

asyncio.run(main())
