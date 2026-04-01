#!/usr/bin/env python3
import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

from scraper.tiktok_shop import TikTokShopScraper


async def main():
    scraper = TikTokShopScraper()
    probe = await scraper.probe_proxy()
    print(json.dumps(probe, indent=2, ensure_ascii=False))

    sample_url = "https://www.tiktok.com/@test/video/123"
    try:
        products = await scraper.detect_products_in_video(sample_url)
        print(json.dumps({
            "sample_url": sample_url,
            "proxy_mode": scraper.proxy_mode,
            "detected_product_ids": products,
        }, indent=2, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({
            "sample_url": sample_url,
            "proxy_mode": scraper.proxy_mode,
            "error": str(e),
        }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())