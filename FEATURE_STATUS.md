# FEATURE_STATUS.md

## Core Routes
- `/` landing: WORKING
- `/start`: WORKING
- `/signup`: WORKING
- `/signin`: WORKING
- `/payment`: WORKING
- `/api/billing/plans`: WORKING
- `/api/system/config`: WORKING

## Research Features
- `/api/search` (TikTok): WORKING
- `/api/profile` (TikTok): WORKING
- `/api/comments` (TikTok): WORKING, but depends on video validity and TikTok response stability
- Download JSON/CSV: NOT RE-TESTED after latest changes

## Platform Confidence
- TikTok: HIGHER confidence
- Instagram: LOW confidence (login walls / fallback-heavy)
- X/Twitter: LOW-MED confidence (Google discovery + oEmbed style fallback)
- Facebook: LOW-MED confidence
- YouTube: MED confidence for search, lower for intelligence depth

## Product Alignment Status
- Search engine: exists
- Content intelligence positioning: PARTIAL
- Hook extraction: BASIC
- Content extraction: BASIC
- Transcript handling: PARTIAL / platform-dependent
- Comment intelligence: BASIC
- True insight layer: MISSING

## Biggest Gaps
1. Product copy still feels like generic scraper
2. Intelligence layer (patterning/summaries) not productized yet
3. Multi-platform quality inconsistent
4. No durable feature/demo docs before this commit
