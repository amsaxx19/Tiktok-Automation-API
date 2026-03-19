# TASKS.md

## Current Goal
Ubah `Playground` dari generic multi-platform scraper menjadi **social media content intelligence tool** untuk creator & affiliate marketer Indonesia.

## Done
- Audit cepat repo dan stack
- Validasi core TikTok search/profile/comments via `.venv`
- Tambah alias API params yang lebih manusiawi:
  - `/api/search`: `keyword`, `max_results`
  - `/api/profile`: `max_results`
  - `/api/comments`: `video_url`, `max_comments`, optional `platform`
- Tambah smoke test script: `scripts_smoke_test.py`
- Verifikasi smoke test lulus untuk landing + billing plans + system config + TikTok search/profile/comments

## In Progress
1. Align positioning/copy/routes ke strategy doc (content intelligence, bukan analytics/sales estimator)
2. Audit frontend/app wording yang masih generic scraper
3. Tentukan fitur inti v1 yang paling sesuai dengan positioning baru
4. Apply practical security baseline from `SECURITY.md`
5. Improve mobile web UX so the product is usable on phones, not just desktop

## Next
- Tambah page/section yang menonjolkan: hooks, captions, transcripts, hashtags, sounds
- Kurangi wording yang terlalu generic / terlalu analytics-ish
- Audit platform lain (IG/X/Facebook) dan putuskan mana yang dipertahankan untuk MVP
- Tambah feature status dan demo flow docs
- Implement security headers, safer downloads, and rate limiting
