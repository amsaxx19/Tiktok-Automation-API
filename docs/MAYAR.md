# Mayar Integration Notes

## Current Direction

Sinyal uses Mayar first for MVP billing so launch is not blocked by a slower direct-gateway verification flow.

## Environment Variables

- `MAYAR_URL_RINGAN`
- `MAYAR_URL_TUMBUH`
- `MAYAR_URL_TIM`
- `MAYAR_WEBHOOK_SECRET`

## Current Routes

- `/payment`
- `/checkout/ringan`
- `/checkout/tumbuh`
- `/checkout/tim`
- `/api/billing/plans`
- `/api/payment/webhook/mayar`

## Expected MVP Flow

1. Create product or checkout page in Mayar for each plan.
2. Put the public checkout URL into the matching env var.
3. User clicks plan button on `/payment`.
4. App redirects user to Mayar.
5. Mayar sends webhook to `/api/payment/webhook/mayar`.
6. Backend verifies payload and writes transaction + subscription status to Postgres.
7. App unlocks access based on the active subscription.

## Notes

- Current app routes already redirect `/checkout/<plan>` to the matching Mayar URL when the env is filled.
- If the URL is still empty, the route intentionally returns a clear setup message instead of failing silently.
- Webhook route now already stores transaction + subscription state through Supabase REST when `SUPABASE_SERVICE_ROLE_KEY` is filled.
- Webhook secret checking currently supports a shared secret sent via `x-webhook-secret`, `x-mayar-webhook-secret`, or `Authorization: Bearer <secret>`.

## What Still Needs Implementation

- Confirm final header/secret format from Mayar production webhook settings
- Add a more deterministic invoice-to-user reference if Mayar supports custom metadata/reference fields
- Add retry-safe idempotency constraint for invoice sync
- Expose billing state and usage in the app UI
