#!/usr/bin/env python3
import asyncio
import os
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from scraper.tiktok import TikTokScraper
from scraper.youtube import YouTubeScraper
from scraper.instagram import InstagramScraper
from scraper.twitter import TwitterScraper
from scraper.facebook import FacebookScraper
from scraper.models import save_results

app = FastAPI(title="ScrapeFlow - Riset Sosmed Tercepat")
executor = ThreadPoolExecutor(max_workers=5)

STATIC_DIR = Path(__file__).parent / "static"

SCRAPERS = {
    "tiktok": TikTokScraper,
    "youtube": YouTubeScraper,
    "instagram": InstagramScraper,
    "twitter": TwitterScraper,
    "facebook": FacebookScraper,
}


@app.get("/", response_class=HTMLResponse)
async def landing():
    landing_file = STATIC_DIR / "landing.html"
    return HTMLResponse(landing_file.read_text(encoding="utf-8"))


@app.get("/app", response_class=HTMLResponse)
async def dashboard():
    return HTML_UI


@app.get("/api/search")
async def search(
    q: str = Query(...),
    platforms: str = Query("tiktok,youtube,instagram,twitter,facebook"),
    max: int = Query(5, ge=1, le=50),
    sort: str = Query("relevance"),
    min_likes: int | None = Query(None),
    max_likes: int | None = Query(None),
):
    platform_list = [p.strip() for p in platforms.split(",") if p.strip() in SCRAPERS]
    if not platform_list:
        return JSONResponse({"error": "No valid platforms"}, 400)

    # Support multiple keywords separated by newlines
    keywords = [k.strip() for k in q.split("\n") if k.strip()]
    if not keywords:
        return JSONResponse({"error": "No keywords provided"}, 400)

    loop = asyncio.get_event_loop()
    all_results = []
    start = time.time()

    async def scrape_platform(name, keyword):
        scraper = SCRAPERS[name]()
        return await loop.run_in_executor(executor, partial(scraper.search, keyword, max))

    tasks = [
        scrape_platform(p, kw)
        for kw in keywords
        for p in platform_list
    ]
    results_list = await asyncio.gather(*tasks, return_exceptions=True)

    for result in results_list:
        if isinstance(result, Exception):
            print(f"Error: {result}")
        elif result:
            all_results.extend(result)

    # Apply filters
    if min_likes is not None:
        all_results = [r for r in all_results if (r.likes or 0) >= min_likes]
    if max_likes is not None:
        all_results = [r for r in all_results if (r.likes or 0) <= max_likes]

    # Apply sort
    if sort == "popular":
        all_results.sort(key=lambda r: r.views or 0, reverse=True)
    elif sort == "latest":
        all_results.sort(key=lambda r: r.upload_date or "", reverse=True)
    elif sort == "most_liked":
        all_results.sort(key=lambda r: r.likes or 0, reverse=True)

    elapsed = time.time() - start

    json_file = csv_file = None
    if all_results:
        json_file, csv_file = save_results(all_results, keywords[0])

    return {
        "keywords": keywords,
        "platforms": platform_list,
        "total": len(all_results),
        "elapsed": f"{elapsed:.1f}s",
        "json_file": json_file,
        "csv_file": csv_file,
        "results": [r.to_dict() for r in all_results],
    }


@app.get("/api/profile")
async def profile(
    username: str = Query(...),
    max: int = Query(10, ge=1, le=50),
    sort: str = Query("latest"),
):
    loop = asyncio.get_event_loop()
    start = time.time()

    scraper = TikTokScraper()
    results = await loop.run_in_executor(
        executor, partial(scraper.scrape_profile, username, max, sort)
    )

    elapsed = time.time() - start
    json_file = csv_file = None
    if results:
        json_file, csv_file = save_results(results, f"profile_{username}")

    return {
        "username": username,
        "total": len(results),
        "elapsed": f"{elapsed:.1f}s",
        "json_file": json_file,
        "csv_file": csv_file,
        "results": [r.to_dict() for r in results],
    }


@app.get("/api/comments")
async def comments(
    url: str = Query(...),
    max: int = Query(50, ge=1, le=200),
):
    loop = asyncio.get_event_loop()
    scraper = TikTokScraper()
    result = await loop.run_in_executor(
        executor, partial(scraper.scrape_comments, url, max)
    )
    return {"url": url, "total": len(result), "comments": result}


@app.get("/api/download")
async def download(file: str = Query(...)):
    if not file.startswith("output/") or ".." in file:
        return JSONResponse({"error": "Invalid file path"}, 400)
    if not os.path.exists(file):
        return JSONResponse({"error": "File not found"}, 404)
    return FileResponse(file, filename=os.path.basename(file))


HTML_UI = """<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ScrapeFlow - Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
:root {
  --bg: #0a0a0f;
  --bg-card: #12121a;
  --bg-card-hover: #1a1a25;
  --bg-input: #12121a;
  --border: #1e1e2e;
  --border-hover: #2e2e42;
  --text: #f0f0f5;
  --text-secondary: #9d9db5;
  --text-muted: #5c5c7a;
  --primary: #7c5cfc;
  --primary-hover: #9b82fc;
  --primary-bg: rgba(124,92,252,0.12);
  --accent-tiktok: #ff0050;
  --accent-youtube: #ff0000;
  --accent-instagram: #e040fb;
  --accent-twitter: #1d9bf0;
  --accent-facebook: #1877f2;
  --success: #34d399;
  --radius: 14px;
  --radius-sm: 10px;
  --radius-lg: 18px;
}

* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: 'Plus Jakarta Sans', -apple-system, sans-serif;
  background: var(--bg);
  color: var(--text);
  min-height: 100vh;
  overflow-x: hidden;
}

/* Subtle grid background */
body::before {
  content: '';
  position: fixed;
  inset: 0;
  background-image:
    radial-gradient(ellipse at 20% 50%, rgba(124,92,252,0.04) 0%, transparent 50%),
    radial-gradient(circle at 1px 1px, rgba(255,255,255,0.015) 1px, transparent 0);
  background-size: 100% 100%, 32px 32px;
  pointer-events: none;
  z-index: 0;
}

.app { position: relative; z-index: 1; }

/* NAV */
nav {
  border-bottom: 1px solid var(--border);
  padding: 16px 32px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  backdrop-filter: blur(20px);
  background: rgba(9,9,11,0.8);
  position: sticky;
  top: 0;
  z-index: 100;
}
.logo {
  font-size: 20px;
  font-weight: 800;
  letter-spacing: -0.5px;
}
.logo span { color: var(--primary); }
.nav-links { display: flex; gap: 4px; }
.nav-link {
  padding: 8px 16px;
  border-radius: var(--radius-sm);
  font-size: 14px;
  font-weight: 500;
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.2s;
  border: none;
  background: none;
}
.nav-link:hover, .nav-link.active { color: var(--text); background: var(--bg-card); }

/* HERO */
.hero {
  text-align: center;
  padding: 60px 32px 40px;
  max-width: 700px;
  margin: 0 auto;
}
.hero h1 {
  font-size: 44px;
  font-weight: 800;
  letter-spacing: -1.5px;
  line-height: 1.1;
  margin-bottom: 16px;
}
.hero h1 .gradient {
  background: linear-gradient(135deg, #6366f1, #a855f7, #ec4899);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}
.hero p { color: var(--text-secondary); font-size: 17px; line-height: 1.6; max-width: 500px; margin: 0 auto; }

/* MAIN CONTAINER */
.main { max-width: 1000px; margin: 0 auto; padding: 0 32px 60px; }

/* TABS / PAGES */
.page { display: none; }
.page.active { display: block; }

/* SECTION CARD */
.section {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 28px;
  margin-bottom: 20px;
}
.section-title {
  font-size: 15px;
  font-weight: 600;
  margin-bottom: 20px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.section-title .icon {
  width: 32px;
  height: 32px;
  border-radius: var(--radius-sm);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
}

/* FORM ELEMENTS */
.form-group { margin-bottom: 16px; }
.form-label {
  display: block;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
  margin-bottom: 6px;
}
.form-hint {
  font-size: 12px;
  color: var(--text-muted);
  margin-top: 4px;
}

input[type="text"], input[type="number"], textarea, select {
  width: 100%;
  padding: 10px 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg);
  color: var(--text);
  font-size: 14px;
  font-family: inherit;
  outline: none;
  transition: border-color 0.2s;
}
input:focus, textarea:focus, select:focus { border-color: var(--primary); }
textarea { resize: vertical; min-height: 100px; }
select { cursor: pointer; appearance: none; background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' fill='%2371717a' viewBox='0 0 16 16'%3E%3Cpath d='M8 11L3 6h10z'/%3E%3C/svg%3E"); background-repeat: no-repeat; background-position: right 12px center; padding-right: 36px; }

/* CHIPS / TOGGLES */
.chip-group { display: flex; gap: 8px; flex-wrap: wrap; }
.chip {
  padding: 8px 16px;
  border: 1px solid var(--border);
  border-radius: 100px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s;
  user-select: none;
  display: flex;
  align-items: center;
  gap: 6px;
}
.chip:hover { border-color: var(--border-hover); }
.chip.active { border-color: var(--primary); background: var(--primary-bg); color: var(--primary-hover); }
.chip .dot { width: 8px; height: 8px; border-radius: 50%; }

/* INLINE GRID */
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.grid-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; }
@media (max-width: 640px) { .grid-2, .grid-3 { grid-template-columns: 1fr; } }

/* BUTTON */
.btn {
  padding: 12px 24px;
  border: none;
  border-radius: var(--radius-sm);
  font-size: 14px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.2s;
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.btn-primary {
  background: var(--primary);
  color: white;
  width: 100%;
  justify-content: center;
  padding: 14px;
  font-size: 15px;
  border-radius: var(--radius);
}
.btn-primary:hover { background: var(--primary-hover); }
.btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-secondary {
  background: var(--bg-card);
  color: var(--text);
  border: 1px solid var(--border);
}
.btn-secondary:hover { background: var(--bg-card-hover); border-color: var(--border-hover); }

/* STATUS BAR */
.status-bar {
  padding: 14px 20px;
  border-radius: var(--radius);
  background: var(--bg-card);
  border: 1px solid var(--border);
  margin-bottom: 20px;
  display: none;
  align-items: center;
  gap: 12px;
  font-size: 14px;
}
.status-bar.active { display: flex; }
.spinner {
  width: 18px; height: 18px;
  border: 2px solid var(--border);
  border-top-color: var(--primary);
  border-radius: 50%;
  animation: spin 0.7s linear infinite;
  flex-shrink: 0;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* RESULTS */
.stats-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 12px;
  margin-bottom: 20px;
}
.stat-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 20px;
  text-align: center;
}
.stat-card .value { font-size: 28px; font-weight: 700; }
.stat-card .label { font-size: 12px; color: var(--text-muted); margin-top: 4px; text-transform: uppercase; letter-spacing: 0.5px; }
.stat-card.tiktok .value { color: var(--accent-tiktok); }
.stat-card.youtube .value { color: var(--accent-youtube); }
.stat-card.instagram .value { color: var(--accent-instagram); }
.stat-card.twitter .value { color: var(--accent-twitter); }
.stat-card.facebook .value { color: var(--accent-facebook); }

.download-row {
  display: flex;
  gap: 10px;
  margin-bottom: 20px;
}
.download-btn {
  padding: 10px 20px;
  border-radius: var(--radius-sm);
  font-size: 13px;
  font-weight: 500;
  text-decoration: none;
  border: 1px solid var(--border);
  color: var(--text-secondary);
  background: var(--bg-card);
  transition: all 0.2s;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.download-btn:hover { border-color: var(--primary); color: var(--primary-hover); }

/* RESULT CARDS */
.result-list { display: flex; flex-direction: column; gap: 8px; }
.result-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 16px 20px;
  transition: all 0.15s;
  cursor: default;
}
.result-card:hover { border-color: var(--border-hover); background: var(--bg-card-hover); }
.result-top { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.badge {
  padding: 3px 10px;
  border-radius: 100px;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.badge.tiktok { background: rgba(255,0,80,0.1); color: var(--accent-tiktok); }
.badge.youtube { background: rgba(255,0,0,0.1); color: var(--accent-youtube); }
.badge.instagram { background: rgba(224,64,251,0.1); color: var(--accent-instagram); }
.badge.twitter { background: rgba(29,155,240,0.1); color: var(--accent-twitter); }
.badge.facebook { background: rgba(24,119,242,0.1); color: var(--accent-facebook); }
.result-title {
  font-size: 14px;
  font-weight: 500;
  margin-bottom: 4px;
  line-height: 1.4;
}
.result-title a { color: var(--text); text-decoration: none; }
.result-title a:hover { color: var(--primary-hover); }
.result-meta { font-size: 13px; color: var(--text-muted); margin-bottom: 10px; }
.result-stats {
  display: flex;
  gap: 20px;
  font-size: 12px;
  color: var(--text-secondary);
}
.result-stats .stat { display: flex; align-items: center; gap: 4px; }

/* COMMENT CARDS */
.comment-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 14px 18px;
  margin-bottom: 8px;
}
.comment-user { font-size: 13px; font-weight: 600; color: var(--primary-hover); margin-bottom: 4px; }
.comment-text { font-size: 14px; line-height: 1.5; }
.comment-meta { font-size: 12px; color: var(--text-muted); margin-top: 6px; }
</style>
</head>
<body>
<div class="app">

<nav>
  <div class="logo"><a href="/" style="text-decoration:none;color:var(--text)">Scrape<span>Flow</span></a></div>
  <div class="nav-links">
    <button class="nav-link active" onclick="switchPage('search')">Cari Konten</button>
    <button class="nav-link" onclick="switchPage('profile')">Profil</button>
    <button class="nav-link" onclick="switchPage('comments')">Komentar</button>
  </div>
</nav>

<div class="hero">
  <h1>Riset <span class="gradient">Semua Platform</span> Sekaligus</h1>
  <p>Cari konten viral dari TikTok, YouTube, Instagram, Twitter/X dan Facebook. Gratis, tanpa API key, langsung gas.</p>
</div>

<div class="main">

<!-- ==================== SEARCH PAGE ==================== -->
<div class="page active" id="page-search">

  <div class="section">
    <div class="section-title"><div class="icon" style="background:var(--primary-bg)">🔍</div> Kata Kunci</div>
    <div class="form-group">
      <label class="form-label">Keywords</label>
      <textarea id="keywords" placeholder="Ketik keyword, satu per baris...&#10;resign dari kerja&#10;cara jualan di tiktok&#10;side hustle indonesia"></textarea>
      <div class="form-hint">Bisa masukin banyak keyword sekaligus (satu per baris) buat riset lebih cepat.</div>
    </div>
  </div>

  <div class="section">
    <div class="section-title"><div class="icon" style="background:rgba(168,85,247,0.1)">📱</div> Platform</div>
    <div class="chip-group">
      <div class="chip active" data-platform="tiktok" onclick="toggleChip(this)"><span class="dot" style="background:var(--accent-tiktok)"></span>TikTok</div>
      <div class="chip active" data-platform="youtube" onclick="toggleChip(this)"><span class="dot" style="background:var(--accent-youtube)"></span>YouTube</div>
      <div class="chip active" data-platform="instagram" onclick="toggleChip(this)"><span class="dot" style="background:var(--accent-instagram)"></span>Instagram</div>
      <div class="chip active" data-platform="twitter" onclick="toggleChip(this)"><span class="dot" style="background:var(--accent-twitter)"></span>Twitter/X</div>
      <div class="chip active" data-platform="facebook" onclick="toggleChip(this)"><span class="dot" style="background:var(--accent-facebook)"></span>Facebook</div>
    </div>
  </div>

  <div class="section">
    <div class="section-title"><div class="icon" style="background:rgba(236,72,153,0.1)">⚙️</div> Filter & Urutan</div>
    <div class="grid-3">
      <div class="form-group">
        <label class="form-label">Jumlah hasil per platform</label>
        <input type="number" id="maxResults" value="5" min="1" max="50">
      </div>
      <div class="form-group">
        <label class="form-label">Urut berdasarkan</label>
        <select id="sortBy">
          <option value="relevance">Paling relevan</option>
          <option value="popular">Views terbanyak</option>
          <option value="most_liked">Likes terbanyak</option>
          <option value="latest">Terbaru</option>
        </select>
      </div>
      <div class="form-group">
        <label class="form-label">&nbsp;</label>
        <div style="height:42px"></div>
      </div>
    </div>
    <div class="grid-2">
      <div class="form-group">
        <label class="form-label">Min likes</label>
        <input type="number" id="minLikes" placeholder="No minimum">
      </div>
      <div class="form-group">
        <label class="form-label">Max likes</label>
        <input type="number" id="maxLikes" placeholder="No maximum">
      </div>
    </div>
  </div>

  <button class="btn btn-primary" id="searchBtn" onclick="doSearch()">Mulai Scraping 🚀</button>

  <div style="height:24px"></div>
  <div class="status-bar" id="searchStatus"><div class="spinner"></div><span id="searchStatusText"></span></div>
  <div id="searchStats"></div>
  <div id="searchDownloads"></div>
  <div id="searchResults" class="result-list"></div>
</div>

<!-- ==================== PROFILE PAGE ==================== -->
<div class="page" id="page-profile">

  <div class="section">
    <div class="section-title"><div class="icon" style="background:rgba(255,0,80,0.1)">👤</div> TikTok Profile Scraper</div>
    <div class="grid-2">
      <div class="form-group">
        <label class="form-label">Username</label>
        <input type="text" id="profileUsername" placeholder="@username atau username">
      </div>
      <div class="form-group">
        <label class="form-label">Maks video</label>
        <input type="number" id="profileMax" value="10" min="1" max="50">
      </div>
    </div>
    <div class="form-group">
      <label class="form-label">Urutan video</label>
      <select id="profileSort">
        <option value="latest">Terbaru</option>
        <option value="popular">Terpopuler</option>
        <option value="oldest">Terlama</option>
      </select>
    </div>
  </div>

  <button class="btn btn-primary" id="profileBtn" onclick="doProfile()">Scrape Profil 🚀</button>

  <div style="height:24px"></div>
  <div class="status-bar" id="profileStatus"><div class="spinner"></div><span id="profileStatusText"></span></div>
  <div id="profileStats"></div>
  <div id="profileDownloads"></div>
  <div id="profileResults" class="result-list"></div>
</div>

<!-- ==================== COMMENTS PAGE ==================== -->
<div class="page" id="page-comments">

  <div class="section">
    <div class="section-title"><div class="icon" style="background:rgba(34,197,94,0.1)">💬</div> TikTok Comment Extractor</div>
    <div class="grid-2">
      <div class="form-group">
        <label class="form-label">URL Video</label>
        <input type="text" id="commentUrl" placeholder="https://www.tiktok.com/@user/video/123...">
      </div>
      <div class="form-group">
        <label class="form-label">Maks komentar</label>
        <input type="number" id="commentMax" value="50" min="1" max="200">
      </div>
    </div>
  </div>

  <button class="btn btn-primary" id="commentBtn" onclick="doComments()">Ambil Komentar 🚀</button>

  <div style="height:24px"></div>
  <div class="status-bar" id="commentStatus"><div class="spinner"></div><span id="commentStatusText"></span></div>
  <div id="commentResults"></div>
</div>

</div><!-- .main -->
</div><!-- .app -->

<script>
function switchPage(name) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-link').forEach(n => n.classList.remove('active'));
  document.getElementById('page-' + name).classList.add('active');
  event.target.classList.add('active');
}

function toggleChip(el) { el.classList.toggle('active'); }

function fmt(n) {
  if (n == null) return '\u2014';
  if (n >= 1e6) return (n/1e6).toFixed(1) + 'M';
  if (n >= 1e3) return (n/1e3).toFixed(1) + 'K';
  return n.toLocaleString();
}

function showStatus(id, text) {
  const bar = document.getElementById(id);
  bar.classList.add('active');
  // Re-add spinner if missing
  if (!bar.querySelector('.spinner')) {
    const sp = document.createElement('div');
    sp.className = 'spinner';
    bar.prepend(sp);
  }
  bar.querySelector('span').textContent = text;
}
function hideStatus(id, text) {
  const bar = document.getElementById(id);
  if (text) bar.querySelector('span').textContent = text;
  const sp = bar.querySelector('.spinner');
  if (sp) sp.remove();
}

function renderCards(containerId, results) {
  document.getElementById(containerId).innerHTML = results.map(r => `
    <div class="result-card">
      <div class="result-top">
        <span class="badge ${r.platform}">${r.platform}</span>
        <span style="font-size:12px;color:var(--text-muted)">${r.duration ? r.duration + 's' : ''}</span>
      </div>
      <div class="result-title"><a href="${r.video_url}" target="_blank">${r.title || r.description?.slice(0,120) || r.video_url}</a></div>
      <div class="result-meta">@${r.author || 'unknown'}${r.music ? ' &middot; ' + r.music : ''}</div>
      <div class="result-stats">
        <span class="stat"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg> ${fmt(r.views)}</span>
        <span class="stat"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg> ${fmt(r.likes)}</span>
        <span class="stat"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg> ${fmt(r.comments)}</span>
        <span class="stat"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/><polyline points="16 6 12 2 8 6"/><line x1="12" y1="2" x2="12" y2="15"/></svg> ${fmt(r.shares)}</span>
        <span class="stat"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg> ${fmt(r.saves)}</span>
      </div>
    </div>
  `).join('');
}

function renderStats(containerId, results) {
  const platforms = {};
  results.forEach(r => {
    if (!platforms[r.platform]) platforms[r.platform] = { count: 0, views: 0 };
    platforms[r.platform].count++;
    platforms[r.platform].views += r.views || 0;
  });
  let html = '<div class="stats-row">';
  html += `<div class="stat-card"><div class="value" style="color:var(--primary)">${results.length}</div><div class="label">Total Hasil</div></div>`;
  for (const [p, s] of Object.entries(platforms)) {
    html += `<div class="stat-card ${p}"><div class="value">${s.count}</div><div class="label">${p} &middot; ${fmt(s.views)} views</div></div>`;
  }
  html += '</div>';
  document.getElementById(containerId).innerHTML = html;
}

function renderDownloads(containerId, json_file, csv_file) {
  if (!json_file) return;
  document.getElementById(containerId).innerHTML = `
    <div class="download-row">
      <a class="download-btn" href="/api/download?file=${encodeURIComponent(json_file)}">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
        JSON
      </a>
      <a class="download-btn" href="/api/download?file=${encodeURIComponent(csv_file)}">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
        CSV
      </a>
    </div>`;
}

/* ========== SEARCH ========== */
async function doSearch() {
  const raw = document.getElementById('keywords').value.trim();
  if (!raw) return;
  const platforms = [...document.querySelectorAll('#page-search .chip.active')].map(c => c.dataset.platform);
  if (!platforms.length) { alert('Pilih minimal satu platform dulu!'); return; }

  const btn = document.getElementById('searchBtn');
  btn.disabled = true; btn.textContent = 'Lagi scraping...';

  const params = new URLSearchParams({
    q: raw,
    platforms: platforms.join(','),
    max: document.getElementById('maxResults').value || 5,
    sort: document.getElementById('sortBy').value,
  });
  const minL = document.getElementById('minLikes').value;
  const maxL = document.getElementById('maxLikes').value;
  if (minL) params.set('min_likes', minL);
  if (maxL) params.set('max_likes', maxL);

  showStatus('searchStatus', 'Lagi scraping ' + platforms.join(', ') + '...');
  document.getElementById('searchStats').innerHTML = '';
  document.getElementById('searchDownloads').innerHTML = '';
  document.getElementById('searchResults').innerHTML = '';

  try {
    const resp = await fetch('/api/search?' + params);
    const data = await resp.json();
    if (data.error) { hideStatus('searchStatus', 'Error: ' + data.error); return; }
    hideStatus('searchStatus', `Selesai! ${data.total} hasil dalam ${data.elapsed}`);
    renderStats('searchStats', data.results);
    renderDownloads('searchDownloads', data.json_file, data.csv_file);
    renderCards('searchResults', data.results);
  } catch(e) {
    hideStatus('searchStatus', 'Error: ' + e.message);
  } finally {
    btn.disabled = false; btn.textContent = 'Mulai Scraping 🚀';
  }
}

/* ========== PROFILE ========== */
async function doProfile() {
  const username = document.getElementById('profileUsername').value.trim();
  if (!username) return;

  const btn = document.getElementById('profileBtn');
  btn.disabled = true; btn.textContent = 'Lagi scraping...';
  showStatus('profileStatus', `Lagi scraping @${username}...`);
  document.getElementById('profileStats').innerHTML = '';
  document.getElementById('profileDownloads').innerHTML = '';
  document.getElementById('profileResults').innerHTML = '';

  try {
    const params = new URLSearchParams({
      username,
      max: document.getElementById('profileMax').value || 10,
      sort: document.getElementById('profileSort').value,
    });
    const resp = await fetch('/api/profile?' + params);
    const data = await resp.json();
    hideStatus('profileStatus', `Selesai! ${data.total} video dalam ${data.elapsed}`);
    renderStats('profileStats', data.results);
    renderDownloads('profileDownloads', data.json_file, data.csv_file);
    renderCards('profileResults', data.results);
  } catch(e) {
    hideStatus('profileStatus', 'Error: ' + e.message);
  } finally {
    btn.disabled = false; btn.textContent = 'Scrape Profil 🚀';
  }
}

/* ========== COMMENTS ========== */
async function doComments() {
  const url = document.getElementById('commentUrl').value.trim();
  if (!url) return;

  const btn = document.getElementById('commentBtn');
  btn.disabled = true; btn.textContent = 'Lagi ngambil...';
  showStatus('commentStatus', 'Lagi extract komentar...');
  document.getElementById('commentResults').innerHTML = '';

  try {
    const params = new URLSearchParams({
      url,
      max: document.getElementById('commentMax').value || 50,
    });
    const resp = await fetch('/api/comments?' + params);
    const data = await resp.json();
    hideStatus('commentStatus', `Selesai! ${data.total} komentar berhasil diambil`);

    document.getElementById('commentResults').innerHTML = data.comments.map(c => `
      <div class="comment-card">
        <div class="comment-user">@${c.user || c.nickname || 'anonymous'}</div>
        <div class="comment-text">${c.text}</div>
        <div class="comment-meta">${c.likes ? c.likes + ' likes' : ''} ${c.replies ? '&middot; ' + c.replies + ' replies' : ''}</div>
      </div>
    `).join('') || '<p style="color:var(--text-muted);padding:20px">Komentar ga ketemu di data halaman. TikTok mungkin load komentar secara dinamis setelah scroll.</p>';
  } catch(e) {
    hideStatus('commentStatus', 'Error: ' + e.message);
  } finally {
    btn.disabled = false; btn.textContent = 'Ambil Komentar 🚀';
  }
}
</script>
</body>
</html>"""


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
