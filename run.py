#!/usr/bin/env python3
"""
Social Media Scraper - Search by keyword across all platforms.

Usage:
    python run.py "your keyword"
    python run.py "your keyword" --platforms tiktok youtube
    python run.py "your keyword" --max 10
    python run.py "your keyword" --platforms tiktok --max 5
"""

import argparse
import sys
import time
from scraper.models import save_results, VideoResult
from scraper.tiktok import TikTokScraper
from scraper.youtube import YouTubeScraper
from scraper.instagram import InstagramScraper
from scraper.twitter import TwitterScraper
from scraper.facebook import FacebookScraper


SCRAPERS = {
    "tiktok": TikTokScraper,
    "youtube": YouTubeScraper,
    "instagram": InstagramScraper,
    "twitter": TwitterScraper,
    "facebook": FacebookScraper,
}


def main():
    parser = argparse.ArgumentParser(description="Search & scrape social media by keyword")
    parser.add_argument("keyword", help="Search keyword")
    parser.add_argument(
        "--platforms", "-p",
        nargs="+",
        choices=list(SCRAPERS.keys()),
        default=list(SCRAPERS.keys()),
        help="Platforms to scrape (default: all)",
    )
    parser.add_argument(
        "--max", "-m",
        type=int,
        default=10,
        help="Max results per platform (default: 10)",
    )
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"  Social Media Scraper")
    print(f"  Keyword: {args.keyword}")
    print(f"  Platforms: {', '.join(args.platforms)}")
    print(f"  Max per platform: {args.max}")
    print(f"{'='*60}\n")

    all_results: list[VideoResult] = []
    start = time.time()

    for platform_name in args.platforms:
        print(f"\n{'─'*40}")
        scraper_cls = SCRAPERS[platform_name]
        scraper = scraper_cls()
        try:
            results = scraper.search(args.keyword, max_results=args.max)
            all_results.extend(results)
        except Exception as e:
            print(f"[{platform_name.upper()}] Error: {e}")

    elapsed = time.time() - start

    # Save results
    if all_results:
        json_path, csv_path = save_results(all_results, args.keyword)
        print(f"\n{'='*60}")
        print(f"  DONE in {elapsed:.1f}s")
        print(f"  Total results: {len(all_results)}")
        print(f"  JSON: {json_path}")
        print(f"  CSV:  {csv_path}")
        print(f"{'='*60}")

        # Summary table
        print(f"\n  {'Platform':<12} {'Count':<8} {'Total Views':<15}")
        print(f"  {'─'*35}")
        for pname in args.platforms:
            p_results = [r for r in all_results if r.platform == pname]
            total_views = sum(r.views or 0 for r in p_results)
            view_str = f"{total_views:,}" if total_views else "N/A"
            print(f"  {pname:<12} {len(p_results):<8} {view_str:<15}")
    else:
        print(f"\n  No results found.")


if __name__ == "__main__":
    main()
