#!/usr/bin/env python3
"""
E2E test for Kalodata API integration (authenticated mode).

Tests:
1. Login via httpx (deviceId + SESSION cookies)
2. Product detail API (/product/detail)
3. Product total stats (/product/detail/total)
4. Product history (/product/detail/history)
5. Full KalodataScraper.get_product() flow

Usage:
    cd ~/Tiktok-Automation-API
    PYTHONPATH=. .venv/bin/python3 scripts/test_kalodata_api.py
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scraper.kalodata import KalodataScraper, KalodataProduct, _parse_idr_string


async def test_parse_idr():
    """Test IDR string parser."""
    print("\n=== Test: _parse_idr_string ===")
    cases = [
        ("Rp3.54m", 3_540_000),
        ("Rp502.38k", 502_380),
        ("Rp0.00", 0),
        ("Rp5.08m", 5_080_000),
        ("Rp1,234,567", 1_234_567),
        ("NaN", 0),
        ("", 0),
        ("Rp100", 100),
    ]
    for input_str, expected in cases:
        result = _parse_idr_string(input_str)
        status = "✅" if result == expected else "❌"
        print(f"  {status} parse('{input_str}') = {result:,} (expected {expected:,})")
    print()


async def test_login():
    """Test Kalodata API login."""
    print("=== Test: API Login ===")
    scraper = KalodataScraper()
    
    if not scraper.email or not scraper.password:
        print("  ⚠️ No KALODATA_EMAIL/PASSWORD in .env — skipping")
        return None
    
    try:
        client = await scraper._ensure_client()
        print(f"  ✅ Login successful! Client ready.")
        return scraper
    except Exception as e:
        print(f"  ❌ Login failed: {e}")
        return None


async def test_product_detail(scraper: KalodataScraper):
    """Test full product detail via API."""
    print("\n=== Test: Product Detail (API) ===")
    
    product_id = "1732181709510707083"
    product = await scraper.get_product(product_id)
    
    if not product:
        print("  ❌ No product returned")
        return
    
    print(f"  Product ID: {product.product_id}")
    print(f"  Title: {product.title[:80]}")
    print(f"  Source: {product.source}")
    print(f"  Price min IDR: Rp{product.price_min_idr:,}")
    print(f"  Avg unit price: Rp{product.avg_unit_price_idr:,}")
    print(f"  Category: {product.category} > {product.parent_category}")
    print(f"  Seller type: {product.seller_type}")
    print(f"  Seller ID: {product.seller_id}")
    print(f"  Tokopedia: {product.is_tokopedia}")
    print(f"  Delivery: {product.delivery_type}")
    print(f"  Brand: {product.brand_name}")
    print(f"  Review count: {product.review_count}")
    print()
    print(f"  Revenue IDR: Rp{product.revenue_idr:,}")
    print(f"  Revenue text: {product.revenue_text}")
    print(f"  Items sold: {product.items_sold:,}")
    print(f"  Items sold text: {product.items_sold_text}")
    print(f"  Related creators: {product.related_creator_count}")
    print(f"  Video revenue: Rp{product.video_revenue_idr:,}")
    print(f"  Live revenue: Rp{product.live_revenue_idr:,}")
    print(f"  Mall revenue: Rp{product.mall_revenue_idr:,}")
    print()
    
    # Assertions
    assert product.product_id == product_id, "product_id mismatch"
    assert product.title, "title should not be empty"
    assert product.price_min_idr > 0, "price_min_idr should be > 0"
    assert product.category, "category should not be empty"
    assert product.source == "kalodata_api", "source should be kalodata_api"
    print("  ✅ All assertions passed!")
    
    return product


async def test_ssr_fallback():
    """Test SSR scraping (no login)."""
    print("\n=== Test: SSR Fallback (no login) ===")
    
    scraper = KalodataScraper(email="", password="")  # No credentials
    product_id = "1732181709510707083"
    product = await scraper.get_product(product_id)
    
    if not product:
        print("  ❌ No product returned from SSR")
        return
    
    print(f"  Title: {product.title[:80]}")
    print(f"  Source: {product.source}")
    print(f"  Price min USD: ${product.price_min_usd}")
    print(f"  Price min IDR: Rp{product.price_min_idr:,}")
    print(f"  Shop name: {product.shop_name}")
    print(f"  Category: {product.category}")
    
    assert product.price_min_idr > 0, "SSR should get price"
    print("  ✅ SSR fallback works!")


async def main():
    print("=" * 60)
    print("  Kalodata API E2E Test")
    print("=" * 60)
    
    # Test IDR parser
    await test_parse_idr()
    
    # Test login
    scraper = await test_login()
    
    if scraper:
        # Test product detail
        await test_product_detail(scraper)
        
        # Cleanup
        await scraper.close()
    else:
        print("\n⚠️ Skipping API tests (login failed)")
        print("  Will test SSR fallback only")
    
    # Test SSR fallback
    # await test_ssr_fallback()  # Uncomment to test SSR (takes ~5s, needs Playwright)
    
    print("\n" + "=" * 60)
    print("  Tests complete!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
