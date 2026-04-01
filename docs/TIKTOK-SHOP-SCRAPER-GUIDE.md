# TikTok Shop Affiliate Scraper — Implementation Guide

> **Approach:** Headless browser + Indonesian residential proxy (NO official TikTok API)  
> **Why no official API:** TikTok Partner API requires seller/developer registration, is login-gated, and designed for managing your OWN shop — not for discovering OTHER people's products.

---

## Overview

**Goal:** Detect dan scrape data produk affiliate (keranjang kuning 🛒) dari video TikTok, tanpa official API.

**Data yang diambil:**
- Product name, price (IDR), discount price
- Jumlah terjual ("10rb+ terjual")
- Rating & review count
- Shop/seller name
- Affiliate commission rate (kalau visible)
- Product thumbnail
- Association video ↔ produk

---

## Step-by-Step Implementation

### Step 1: Setup Indonesian Residential Proxy

TikTok Shop content is **geo-restricted**. Keranjang kuning hanya muncul untuk user Indonesia.

**Option A — ScrapeOps Proxy (recommended for dev)**
```bash
pip install scrapeops-scrapy  # or just use their API directly
```
- Endpoint: `https://proxy.scrapeops.io/v1/?api_key=YOUR_KEY&url=TARGET&country=id`
- Cost: Free tier = 1000 requests/month
- Sign up: https://scrapeops.io

**Option B — Bright Data / Oxylabs (production)**
- Residential proxy pool with Indonesia geo-targeting
- Cost: ~$8-15/GB
- Better reliability, larger IP pool
- Supports SOCKS5 for Playwright

**Option C — Free Proxy Rotation (budget, unreliable)**
```python
# Scrape free Indonesian proxies from public lists
# WARNING: unreliable, slow, often blocked
```

**Recommended:** Start with ScrapeOps free tier for development, upgrade to Bright Data for production.

**New env vars:**
```
PROXY_URL=socks5://user:pass@proxy-host:port
# OR
SCRAPEOPS_API_KEY=your_key_here
PROXY_COUNTRY=id
```

---

### Step 2: Create `scraper/tiktok_shop.py`

This is a NEW file. It scrapes product data from TikTok Shop pages.

**File structure:**
```python
# scraper/tiktok_shop.py

import re
import json
import httpx
from dataclasses import dataclass, asdict
from typing import Optional

@dataclass
class TikTokProduct:
    """Represents a product found in a TikTok video's keranjang kuning."""
    product_id: str
    product_url: str
    name: str = ""
    price: int = 0                  # in IDR
    original_price: int = 0         # before discount
    discount_pct: int = 0
    sold_count: str = ""            # "10rb+ terjual"
    rating: float = 0.0
    review_count: int = 0
    shop_name: str = ""
    shop_url: str = ""
    thumbnail: str = ""
    commission_rate: str = ""       # if visible
    category: str = ""
    video_url: str = ""             # source video

    def to_dict(self):
        return asdict(self)


class TikTokShopScraper:
    """Scrapes TikTok Shop product data from videos with keranjang kuning."""
    
    PRODUCT_URL_PATTERN = re.compile(
        r'https?://(?:www\.)?(?:shop\.tiktok\.com|tiktok\.com/view/product)/(\d+)'
    )
    
    def __init__(self, proxy_url: str = None):
        self.proxy_url = proxy_url
    
    async def detect_products_in_video(self, video_url: str) -> list[str]:
        """
        Detect product IDs linked to a TikTok video.
        Returns list of product_ids.
        """
        # ... implementation below
        pass
    
    async def scrape_product(self, product_id: str, video_url: str = "") -> TikTokProduct | None:
        """
        Scrape full product details from TikTok Shop.
        """
        # ... implementation below
        pass
    
    async def scrape_products_from_video(self, video_url: str) -> list[TikTokProduct]:
        """
        Full pipeline: detect products in video → scrape each product.
        """
        product_ids = await self.detect_products_in_video(video_url)
        products = []
        for pid in product_ids:
            product = await self.scrape_product(pid, video_url)
            if product:
                products.append(product)
        return products
```

---

### Step 3: Implement Product Detection from Video Page

There are **3 methods** to detect products linked to a TikTok video. Try them in order:

#### Method A: Parse `__UNIVERSAL_DATA_FOR_REHYDRATION__` script tag

TikTok embeds video data in a `<script>` tag. Product links may be in the video description or in a dedicated product section.

```python
async def detect_products_in_video(self, video_url: str) -> list[str]:
    """Detect product IDs from video page data."""
    product_ids = []
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
        "Accept-Language": "id-ID,id;q=0.9,en;q=0.5",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    
    async with httpx.AsyncClient(
        timeout=20,
        proxy=self.proxy_url,  # Indonesian proxy
        follow_redirects=True,
    ) as client:
        resp = await client.get(video_url, headers=headers)
        html = resp.text
    
    # Method A: Parse __UNIVERSAL_DATA_FOR_REHYDRATION__
    match = re.search(
        r'<script[^>]*id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
        html, re.DOTALL
    )
    if match:
        try:
            data = json.loads(match.group(1))
            # Navigate to video item data
            # Structure: __DEFAULT_SCOPE__."webapp.video-detail".itemInfo.itemStruct
            default_scope = data.get("__DEFAULT_SCOPE__", {})
            video_detail = default_scope.get("webapp.video-detail", {})
            item_struct = video_detail.get("itemInfo", {}).get("itemStruct", {})
            
            # Check for product anchors / shopping info
            # TikTok stores product info in different paths depending on version:
            anchors = item_struct.get("anchors", []) or []
            for anchor in anchors:
                if anchor.get("type") == "product":
                    pid = anchor.get("id", "")
                    if pid:
                        product_ids.append(pid)
            
            # Also check description for shop.tiktok.com links
            desc = item_struct.get("desc", "")
            product_ids.extend(self.PRODUCT_URL_PATTERN.findall(desc))
            
        except (json.JSONDecodeError, KeyError):
            pass
    
    # Method B: Regex scan full HTML for product URLs
    found_in_html = self.PRODUCT_URL_PATTERN.findall(html)
    product_ids.extend(found_in_html)
    
    # Method C: Look for TikTok's internal API call patterns
    # TikTok sometimes includes product data in SIGI_STATE or other script tags
    sigi_match = re.search(r'<script[^>]*>.*?SIGI_STATE.*?=\s*({.*?})\s*;?\s*</script>', html, re.DOTALL)
    if sigi_match:
        try:
            sigi = json.loads(sigi_match.group(1))
            # Look for product references in SIGI state
            items = sigi.get("ItemModule", {})
            for vid, vdata in items.items():
                for anchor in vdata.get("anchors", []) or []:
                    pid = anchor.get("id", "")
                    if pid:
                        product_ids.append(pid)
        except:
            pass
    
    return list(set(product_ids))  # deduplicate
```

#### Method B (Fallback): Use Scrapling headless browser

If httpx doesn't get the full page (client-side rendered), use Scrapling:

```python
from scrapling import AsyncStealthySession

async def detect_products_headless(self, video_url: str) -> list[str]:
    """Use headless browser to detect products (handles client-side rendering)."""
    product_ids = []
    
    session = AsyncStealthySession(
        proxy=self.proxy_url,
        # Set locale to Indonesia
        extra_headers={"Accept-Language": "id-ID,id;q=0.9"},
    )
    
    page = await session.get(video_url)
    html = str(page.html)
    
    # Look for keranjang kuning button / product anchor
    # The shopping bag icon / product card is usually in a specific container
    product_links = page.css('a[href*="shop.tiktok.com"], a[href*="/view/product/"]')
    for link in product_links:
        href = link.attrs.get("href", "")
        found = self.PRODUCT_URL_PATTERN.findall(href)
        product_ids.extend(found)
    
    # Also check the rehydration data
    product_ids.extend(self.PRODUCT_URL_PATTERN.findall(html))
    
    return list(set(product_ids))
```

---

### Step 4: Implement Product Page Scraper

Once you have product IDs, scrape the actual product pages.

**Target URL pattern:** `https://shop.tiktok.com/view/product/{product_id}`

```python
async def scrape_product(self, product_id: str, video_url: str = "") -> TikTokProduct | None:
    """Scrape product details from TikTok Shop product page."""
    
    product_url = f"https://shop.tiktok.com/view/product/{product_id}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36",
        "Accept-Language": "id-ID,id;q=0.9",
    }
    
    try:
        async with httpx.AsyncClient(
            timeout=20,
            proxy=self.proxy_url,
            follow_redirects=True,
        ) as client:
            resp = await client.get(product_url, headers=headers)
            html = resp.text
        
        # Parse product data from script tags
        # TikTok Shop also uses __UNIVERSAL_DATA or similar hydration
        product = TikTokProduct(
            product_id=product_id,
            product_url=product_url,
            video_url=video_url,
        )
        
        # Try JSON-LD structured data first (most reliable if present)
        jsonld_match = re.search(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL)
        if jsonld_match:
            try:
                ld = json.loads(jsonld_match.group(1))
                if isinstance(ld, list):
                    ld = ld[0]
                product.name = ld.get("name", "")
                offers = ld.get("offers", {})
                if isinstance(offers, list):
                    offers = offers[0]
                price_str = offers.get("price", "0")
                product.price = int(float(price_str))
                product.rating = float(ld.get("aggregateRating", {}).get("ratingValue", 0))
                product.review_count = int(ld.get("aggregateRating", {}).get("reviewCount", 0))
                product.thumbnail = ld.get("image", "")
            except:
                pass
        
        # Try hydration script data
        hydration = re.search(
            r'<script[^>]*id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
            html, re.DOTALL
        )
        if hydration:
            try:
                data = json.loads(hydration.group(1))
                # Navigate TikTok Shop's product data structure
                # (exact path may vary — inspect actual page)
                scope = data.get("__DEFAULT_SCOPE__", {})
                product_detail = scope.get("product_detail", {}) or scope.get("webapp.product-detail", {})
                
                if product_detail:
                    product.name = product.name or product_detail.get("title", "")
                    product.price = product.price or int(product_detail.get("price", {}).get("sale_price", 0)) // 100
                    product.original_price = int(product_detail.get("price", {}).get("original_price", 0)) // 100
                    product.sold_count = product_detail.get("sold_count", "")
                    product.shop_name = product_detail.get("seller", {}).get("name", "")
                    product.category = product_detail.get("category", {}).get("name", "")
                    
                    # Commission info (may be in affiliate-specific endpoints)
                    commission = product_detail.get("commission", {})
                    if commission:
                        product.commission_rate = f"{commission.get('rate', 0)}%"
            except:
                pass
        
        # Calculate discount percentage
        if product.original_price > 0 and product.price > 0 and product.price < product.original_price:
            product.discount_pct = int((1 - product.price / product.original_price) * 100)
        
        return product if product.name else None
        
    except Exception as e:
        print(f"[TikTokShop] Error scraping product {product_id}: {e}")
        return None
```

---

### Step 5: Integrate with Existing TikTok Scraper

Modify `scraper/tiktok.py` to optionally detect products when scraping videos.

**In `_parse_item()` or after search results are collected:**

```python
# At top of tiktok.py, add:
from scraper.tiktok_shop import TikTokShopScraper

# In the search() or _parse_item() method, after building VideoResult:
# Add a new field to detect if video has products
async def _check_for_products(self, item: dict) -> list[dict]:
    """Check if video has keranjang kuning products."""
    # Quick check: look for shopping-related indicators in item data
    has_commerce = bool(
        item.get("anchors") or 
        item.get("commerceInfo") or
        item.get("poi") or
        "keranjang" in (item.get("desc", "") or "").lower() or
        "shop" in (item.get("desc", "") or "").lower()
    )
    
    if not has_commerce:
        return []
    
    # Full scrape
    proxy_url = os.getenv("PROXY_URL", "")
    shop_scraper = TikTokShopScraper(proxy_url=proxy_url if proxy_url else None)
    video_url = f"https://www.tiktok.com/@user/video/{item.get('id', '')}"
    products = await shop_scraper.scrape_products_from_video(video_url)
    return [p.to_dict() for p in products]
```

---

### Step 6: Update VideoResult Model

Add product-related fields to `scraper/models.py`:

```python
# Add to VideoResult:
    products: list = field(default_factory=list)     # list of TikTokProduct dicts
    has_affiliate: bool = False                       # quick flag
```

And update `to_dict()` to include these.

---

### Step 7: Update Frontend Insight Cards

Add a new insight card type for affiliate products in the result rendering:

```javascript
// In rowResult() function, after existing insight cards:
if (r.has_affiliate && r.products && r.products.length > 0) {
    // Render product cards
    r.products.forEach(p => {
        html += `<div class="insight-card product-card">
            <div class="insight-label">🛒 Produk Affiliate</div>
            <div class="insight-value">${p.name}</div>
            <div class="product-meta">
                <span class="product-price">Rp${(p.price || 0).toLocaleString('id-ID')}</span>
                ${p.discount_pct ? `<span class="product-discount">-${p.discount_pct}%</span>` : ''}
                ${p.sold_count ? `<span class="product-sold">${p.sold_count}</span>` : ''}
                ${p.commission_rate ? `<span class="product-commission">Komisi: ${p.commission_rate}</span>` : ''}
            </div>
        </div>`;
    });
}
```

---

### Step 8: Add Product Scraping to Search Pipeline (Optional)

In `server.py`, after search results are enriched:

```python
# In the search() handler, after enrich_result_text:
# Only scrape products if user has Pro tier (expensive operation)
if plan and plan.get("code") in ("pro", "lifetime"):
    for result in all_results:
        if result.platform == "tiktok":
            # Quick heuristic check before full scrape
            if any(w in (result.caption or "").lower() for w in ["keranjang", "shop", "link", "beli"]):
                try:
                    shop_scraper = TikTokShopScraper(proxy_url=os.getenv("PROXY_URL"))
                    products = await shop_scraper.scrape_products_from_video(result.video_url)
                    result.products = [p.to_dict() for p in products]
                    result.has_affiliate = len(result.products) > 0
                except Exception as e:
                    print(f"[TikTokShop] Product scrape failed: {e}")
```

**⚠️ Important:** Product scraping is SLOW (1-3s per product page). Only do this for:
- Pro/Lifetime users
- Videos that likely have products (keyword heuristic)
- Optionally: make it a separate endpoint `/api/products?video_url=...`

---

### Step 9: Alternative — Dedicated Product Endpoint

Instead of auto-scraping in search, create a separate endpoint:

```python
@app.get("/api/products")
async def get_video_products(
    request: Request,
    url: str = Query(..., description="TikTok video URL"),
):
    user, plan, denial = await enforce_feature_access(request, "products")
    if denial:
        return denial
    
    proxy_url = os.getenv("PROXY_URL", "")
    scraper = TikTokShopScraper(proxy_url=proxy_url if proxy_url else None)
    products = await scraper.scrape_products_from_video(url)
    
    return {
        "video_url": url,
        "total_products": len(products),
        "products": [p.to_dict() for p in products],
    }
```

User clicks "🛒 Cek Produk" button on a result card → calls this endpoint → shows products inline.

---

## Testing Strategy

### 1. Find a TikTok video with keranjang kuning
```bash
# Search TikTok for Indonesian affiliate content
curl "http://127.0.0.1:8000/api/search?q=review+skincare+murah&platforms=tiktok&max_results=10"
# Look for videos with shopping-related captions
```

### 2. Test product detection
```python
from scraper.tiktok_shop import TikTokShopScraper

scraper = TikTokShopScraper(proxy_url="socks5://user:pass@id-proxy:port")
products = await scraper.detect_products_in_video("https://www.tiktok.com/@user/video/1234567890")
print(products)  # Should return product IDs
```

### 3. Test product scraping
```python
product = await scraper.scrape_product("1234567890")
print(product.to_dict())  # Should return full product data
```

---

## Key Challenges & Mitigations

| Challenge | Mitigation |
|-----------|-----------|
| **TikTok anti-bot** | Use residential proxy + Scrapling stealth mode + mobile User-Agent |
| **Geo-restriction** | Indonesian residential proxy REQUIRED |
| **Rate limiting** | Add delays between requests, proxy rotation |
| **Page structure changes** | Abstract parsing into separate functions, easy to update |
| **Product data not in HTML** | Some data may require API calls TikTok makes client-side — intercept with Playwright network events |
| **Commission rate hidden** | Only visible to affiliate dashboard users — may not be scrapable publicly |
| **Performance** | Product scraping is slow — make it opt-in (separate button/endpoint), cache results |

---

## Estimated Implementation Time

| Step | Time |
|------|------|
| Step 1: Proxy setup | 30 min |
| Step 2-4: Core scraper (`tiktok_shop.py`) | 3-4 hours |
| Step 5: Integration with TikTok scraper | 1 hour |
| Step 6: Model update | 15 min |
| Step 7: Frontend insight cards | 1 hour |
| Step 8-9: Search pipeline / endpoint | 1-2 hours |
| Testing & debugging | 2-3 hours |
| **Total** | **~8-10 hours** |

---

## File Changes Summary

| File | Action |
|------|--------|
| `scraper/tiktok_shop.py` | **NEW** — TikTokProduct dataclass + TikTokShopScraper class |
| `scraper/models.py` | **EDIT** — Add `products` and `has_affiliate` fields to VideoResult |
| `scraper/tiktok.py` | **EDIT** — Optional product detection hook |
| `server.py` | **EDIT** — New `/api/products` endpoint + frontend product card rendering |
| `requirements.txt` | **EDIT** — No new deps needed (using existing httpx + scrapling) |
| `.env` | **EDIT** — Add `PROXY_URL` or `SCRAPEOPS_API_KEY` |

---

*Guide written March 2026 — TikTok page structure may change. Inspect actual pages to verify selectors.*
