"""Quick test for price_enricher parsing functions."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scraper.price_enricher import parse_idr_price, estimate_price_from_description

# Test parse_idr_price
tests = [
    ("Rp 49.000", 49000),
    ("Rp49.000", 49000),
    ("Rp 1.250.000", 1250000),
    ("45rb", 45000),
    ("45 rb", 45000),
    ("35ribu", 35000),
    ("50k", 50000),
    ("Rp49,000", 49000),
]
print("=== parse_idr_price ===")
for text, expected in tests:
    result = parse_idr_price(text)
    status = "OK" if result == expected else "FAIL"
    print(f"  [{status}] {text!r:20s} -> {result:>10,} (expected {expected:,})")

# Test estimate_price_from_description
desc_tests = [
    ("cuma 45rb aja lho!", 45000),
    ("harga Rp 49.000 diskon", 49000),
    ("diskon jadi 35ribu", 35000),
    ("only 50k harga asli", 50000),
    ("murah banget 99rb", 99000),
    ("promo hari ini Rp 125.000", 125000),
    ("video review sofa cleaner", 0),
]
print()
print("=== estimate_price_from_description ===")
for desc, expected in desc_tests:
    result = estimate_price_from_description(desc)
    status = "OK" if result == expected else "FAIL"
    print(f"  [{status}] {desc!r:40s} -> {result:>10,} (expected {expected:,})")
