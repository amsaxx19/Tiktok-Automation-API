# UI_UX_CRON_PLAN.md

Cron plan for improving `Playground` during development (not user-facing product cron jobs).

## Daily (1x / day)

### 1. Smoke test
Run:
- `.venv/bin/python scripts_smoke_test.py`

Purpose:
- catch route breakage fast
- verify TikTok search/profile/comments still work

### 2. UI screenshot check
Capture fresh screenshots for:
- landing
- app dashboard
- signup
- signin
- payment
- mobile dashboard

Purpose:
- detect visual regressions
- keep demo readiness
- support before/after review

### 3. UX review note update
Append/update:
- `FEATURE_STATUS.md`
- `TASKS.md`
- future `UI_REVIEW.md`

Purpose:
- track what still feels admin-ish, generic, or broken

## Weekly

### 1. Dependency audit
Check outdated/vulnerable dependencies.

### 2. Cleanup
Prune temp outputs, screenshots, logs, stale scrape artifacts.

### 3. Platform confidence review
Re-check TikTok / Instagram / X / Facebook / YouTube support quality.

## Not for now
Do not add product-facing recurring scrape jobs yet (watchlists/digests) until core app, UX, and positioning are stable.
