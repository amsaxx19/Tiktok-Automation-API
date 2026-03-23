# HEARTBEAT_NOTES.md

## Current reality
OpenClaw heartbeat behavior for the main session appears configured (`30m`), but from inside the session we cannot reliably force the scheduler to fire on demand.

That means:
- `HEARTBEAT.md` can define what to do **when** a heartbeat arrives
- but it does not guarantee the scheduler will immediately trigger from inside this conversation

## Practical workaround
For `Playground`, do not depend on heartbeat alone for momentum.
Use these instead:
1. Keep `HEARTBEAT.md` updated with the next priorities
2. Maintain `TASKS.md`, `FEATURE_STATUS.md`, `DEMO_FLOW.md`
3. Use local repeatable scripts for progress loops:
   - `scripts_smoke_test.py`
   - future UI audit script
   - future scraper regression script
4. If true background periodic work is needed, use a real macOS scheduled job (launchd/cron) for deterministic tasks like smoke tests and screenshots

## Important distinction
- Heartbeat = good for prompting the agent when OpenClaw sends one
- launchd/cron = good for deterministic recurring engineering tasks

## Recommendation
Do not treat heartbeat as the only background engine for `Playground` development.
Treat it as a bonus trigger.
For guaranteed recurring work, implement scheduled scripts for:
- smoke tests
- screenshot capture
- scraper regression checks
- cleanup
