#!/usr/bin/env python3
"""E2E test: Full pipeline - TikTok video → product detection → Kalodata price enrichment"""
import asyncio
import json
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

async def test_kalodata_standalone():
    """Test 1: Direct Kalodata product lookup"""
    from scraper.kalodata import KalodataScraper
    
    print("=" * 60)
    print("Test 1: Kalodata standalone product lookup")
    print("=" * 60)
    
    ks = KalodataScraper()
    
    # Test product from the user's link
    product = await ks.get_product("1732181709510707083")
    assert product is not None, "Product should not be None"
    assert product.price_min_usd > 0, "Price should be > 0"
    assert product.shop_name, "Shop name should not be empty"
    assert product.category, "Category should not be empty"
    
    print(f"  ✅ Product: {product.title[:60]}...")
    print(f"  ✅ Price: ${product.price_min_usd} - ${product.price_max_usd}")
    print(f"  ✅ IDR: Rp{product.price_idr:,}")
    print(f"  ✅ Shop: {product.shop_name}")
    print(f"  ✅ Category: {product.category}")
    print()


async def test_kalodata_enrichment():
    """Test 2: PriceEnricher with Kalodata integration"""
    from scraper.tiktok_shop import TikTokProduct
    from scraper.price_enricher import PriceEnricher
    
    print("=" * 60)
    print("Test 2: PriceEnricher with Kalodata integration")
    print("=" * 60)
    
    enricher = PriceEnricher()
    
    # Create a product with known Kalodata ID but no price
    product = TikTokProduct(
        product_id="1732181709510707083",
        product_url="https://shop-id.tokopedia.com/pdp/test/1732181709510707083",
        name="Essensi Pencegah Rambut Rontok",
    )
    
    assert product.price == 0, "Price should start at 0"
    assert product.shop_name == "", "Shop should start empty"
    
    result = await enricher.enrich(product)
    
    print(f"  Enriched: {result}")
    print(f"  Price: Rp{product.price:,}")
    print(f"  Shop: {product.shop_name}")
    print(f"  Category: {product.category}")
    
    assert result == True, "Should have been enriched"
    assert product.price > 0, f"Price should be > 0, got {product.price}"
    print(f"  ✅ Product enriched successfully!")
    print()


async def test_full_pipeline():
    """Test 3: Full pipeline — video URL → products → enriched with Kalodata"""
    from scraper.tiktok_shop import TikTokShopScraper
    
    print("=" * 60)
    print("Test 3: Full pipeline (video → products → Kalodata enrichment)")
    print("=" * 60)
    
    # Use a known live video with products
    video_url = "https://www.tiktok.com/@amosthiosa/video/7622536313668979976"
    
    scraper = TikTokShopScraper()
    print(f"  Scraping video: {video_url}", flush=True)
    products = await scraper.scrape_products_from_video(video_url)
    
    print(f"  Products found: {len(products)}")
    
    for i, p in enumerate(products):
        print(f"\n  Product [{i}]:")
        print(f"    Name: {p.name[:60]}...")
        print(f"    Price: Rp{p.price:,}")
        print(f"    Shop: {p.shop_name}")
        print(f"    Category: {p.category}")
        print(f"    URL: {p.product_url[:80]}")
    
    if products:
        print(f"\n  ✅ {len(products)} products with enriched data!")
    else:
        print(f"\n  ⚠️ No products found (video might be dead)")
    print()


async def main():
    print("\n🧪 Kalodata Integration E2E Tests\n")
    
    await test_kalodata_standalone()
    await test_kalodata_enrichment()
    
    # Full pipeline test is optional (slower, requires proxy)
    if "--full" in sys.argv:
        await test_full_pipeline()
    else:
        print("(Skipping full pipeline test. Run with --full to include)")
    
    print("\n✅ All tests passed!\n")


if __name__ == "__main__":
    asyncio.run(main())
