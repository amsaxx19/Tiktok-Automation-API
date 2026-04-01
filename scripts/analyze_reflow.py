#!/usr/bin/env python3
"""Deep dive into reflow JSON to find Rp / price data."""
import json, re
from pathlib import Path

# Find the largest JSON file in price_captures2
out_dir = Path(__file__).resolve().parent / "price_captures2"
if not out_dir.exists():
    out_dir = Path(__file__).resolve().parent / "price_captures"

candidates = sorted(out_dir.glob("*.json"), key=lambda p: p.stat().st_size, reverse=True)
print(f"JSON files in {out_dir}:")
for c in candidates[:10]:
    print(f"  {c.name}: {c.stat().st_size} bytes")

if not candidates:
    print("No JSON files found")
    exit()

# Use the largest one (should be reflow)
target = candidates[0]
print(f"\nAnalyzing: {target.name} ({target.stat().st_size} bytes)")

data = target.read_text()

# Find ALL Rp occurrences
rp_matches = list(re.finditer(r"Rp\s?[\d.,]+", data))
print(f"\nRp occurrences: {len(rp_matches)}")
for m in rp_matches[:30]:
    start = max(0, m.start() - 100)
    end = min(len(data), m.end() + 100)
    context = data[start:end].replace("\n", " ")
    print(f"  [{m.start()}]: ...{context}...")
    print()

# Search for non-zero price fields
print("\n=== Non-zero price-like fields ===")
patterns = [
    (r'"price"\s*:\s*"([1-9][^"]*?)"', "price (string)"),
    (r'"price"\s*:\s*([1-9]\d+)', "price (number)"),
    (r'"market_price"\s*:\s*"([1-9][^"]*?)"', "market_price (string)"),
    (r'"market_price"\s*:\s*([1-9]\d+)', "market_price (number)"),
    (r'"sell_price"\s*:\s*"([1-9][^"]*?)"', "sell_price"),
    (r'"display_price"\s*:\s*"([^"]+?)"', "display_price"),
    (r'"formatted_price"\s*:\s*"([^"]+?)"', "formatted_price"),
    (r'"price_text"\s*:\s*"([^"]+?)"', "price_text"),
    (r'"min_price"\s*:\s*"?([1-9][^",}]*)', "min_price"),
    (r'"max_price"\s*:\s*"?([1-9][^",}]*)', "max_price"),
    (r'"original_price"\s*:\s*"?([1-9][^",}]*)', "original_price"),
    (r'"sku_sell_price"\s*:\s*"?([1-9][^",}]*)', "sku_sell_price"),
    (r'"sku_market_price"\s*:\s*"?([1-9][^",}]*)', "sku_market_price"),
    (r'"origin_price"\s*:\s*"?([1-9][^",}]*)', "origin_price"),
]

for pat, label in patterns:
    matches = re.findall(pat, data)
    if matches:
        print(f"  {label}: {matches[:5]}")

# Parse the JSON and look at structure
print("\n=== JSON structure analysis ===")
try:
    jdata = json.loads(data)
    
    # Look for item_list
    def find_items(obj, path=""):
        if isinstance(obj, dict):
            if "item_list" in obj:
                items = obj["item_list"]
                print(f"\n  item_list at '{path}': {len(items)} items")
                if items:
                    item = items[0]
                    ib = item.get("item_basic", {})
                    print(f"    First item: id={ib.get('id','?')}")
                    
                    # Look at anchors
                    anchors = ib.get("anchors", [])
                    if anchors:
                        for ai, anc in enumerate(anchors[:2]):
                            print(f"\n    Anchor [{ai}]:")
                            extra_str = anc.get("extra", "")
                            if extra_str:
                                try:
                                    extra = json.loads(extra_str)
                                    if isinstance(extra, list) and extra:
                                        inner = extra[0]
                                        if isinstance(inner, dict):
                                            inner_extra = inner.get("extra", "")
                                            if inner_extra:
                                                try:
                                                    prod = json.loads(inner_extra)
                                                    print(f"      title: {prod.get('title','')[:60]}")
                                                    print(f"      price: {prod.get('price')}")
                                                    print(f"      market_price: {prod.get('market_price')}")
                                                    print(f"      currency: {prod.get('currency')}")
                                                    print(f"      sold_count: {prod.get('sold_count')}")
                                                    print(f"      shop_name: {prod.get('shop_name')}")
                                                    
                                                    # DUMP ALL KEYS AND VALUES for price hunting
                                                    print(f"      ALL KEYS: {list(prod.keys())}")
                                                    for k, v in sorted(prod.items()):
                                                        if isinstance(v, (int, float, str)) and v:
                                                            vstr = str(v)
                                                            if len(vstr) < 200:
                                                                print(f"        {k} = {v}")
                                                    
                                                    # Check SKUs
                                                    skus = prod.get("skus", [])
                                                    print(f"      SKUs: {len(skus)}")
                                                    for si, sku in enumerate(skus[:3]):
                                                        print(f"        SKU[{si}]: {json.dumps(sku, ensure_ascii=False)[:300]}")
                                                except:
                                                    pass
                                except:
                                    pass
                    
                    # Also check item_basic directly for price fields
                    print(f"\n    item_basic keys: {list(ib.keys())}")
                    for k, v in ib.items():
                        if "price" in k.lower() or "cost" in k.lower() or "sold" in k.lower():
                            print(f"      {k} = {v}")
                            
            for k, v in obj.items():
                find_items(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj[:3]):
                find_items(v, f"{path}[{i}]")
    
    find_items(jdata)
except json.JSONDecodeError as e:
    print(f"  JSON parse error: {e}")
