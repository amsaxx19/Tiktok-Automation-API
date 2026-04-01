"""End-to-end test: scrape products from video with price enrichment."""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scraper.tiktok_shop import TikTokShopScraper

VIDEO_URL = "https://www.tiktok.com/@amosthiosa/video/7485069092459463953"
PROXY_URL = os.getenv("PROXY_URL", "http://5sjQhR7dWXPoSuv:gAbLujfGLSP2rWU@178.93.21.156:49644")


async def main():
    print(f"Testing scraper with price enrichment")
    print(f"Video: {VIDEO_URL}")
    print(f"Proxy: {PROXY_URL[:40]}...")
    print("=" * 70)

    scraper = TikTokShopScraper(proxy_url=PROXY_URL)
    products = await scraper.scrape_products_from_video(VIDEO_URL)

    print(f"\n{'=' * 70}")
    print(f"RESULTS: {len(products)} product(s)")
    print(f"{'=' * 70}")

    for i, p in enumerate(products, 1):
        d = p.to_dict()
        print(f"\n--- Product {i} ---")
        print(f"  Name:           {p.name[:80]}")
        print(f"  Product ID:     {p.product_id}")
        print(f"  Price:          Rp {p.price:,}" if p.price else "  Price:          (not available)")
        print(f"  Original Price: Rp {p.original_price:,}" if p.original_price else "  Original Price: (not available)")
        print(f"  Discount:       {p.discount_pct}%" if p.discount_pct else "  Discount:       N/A")
        print(f"  Sold:           {p.sold_count}" if p.sold_count else "  Sold:           (not available)")
        print(f"  Shop:           {p.shop_name}" if p.shop_name else "  Shop:           (not available)")
        print(f"  Category:       {p.category}" if p.category else "  Category:       N/A")
        print(f"  URL:            {p.product_url[:80]}")
        print(f"  Thumbnail:      {p.thumbnail[:60]}..." if p.thumbnail else "  Thumbnail:      N/A")
        print(f"  Seller ID:      {d.get('seller_id', 'N/A')}")

    # Export as JSON
    output = [p.to_dict() for p in products]
    outfile = os.path.join(os.path.dirname(__file__), "e2e_result.json")
    with open(outfile, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nSaved to {outfile}")


if __name__ == "__main__":
    asyncio.run(main())
