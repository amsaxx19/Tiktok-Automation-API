#!/usr/bin/env python3
"""Quick test of the reflow API product scraper."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from scraper.tiktok_shop import TikTokShopScraper

VIDEO_URLS = [
    "https://www.tiktok.com/@amosthiosa/video/7622536313668979976",
    "https://www.tiktok.com/@amosthiosa/video/7622303845783309575",
    "https://www.tiktok.com/@amosthiosa/video/7621873066007661842",
    "https://www.tiktok.com/@amosthiosa/video/7621558447976303879",
]


async def main():
    url = sys.argv[1] if len(sys.argv) > 1 else VIDEO_URLS[0]
    print(f"🔍 Scraping: {url}", flush=True)

    s = TikTokShopScraper()
    products = await s.scrape_products_from_video(url)

    print(f"\n✅ Found {len(products)} product(s):", flush=True)
    for i, p in enumerate(products):
        d = p.to_dict()
        print(f"\n--- Product {i+1} ---", flush=True)
        print(json.dumps(d, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
