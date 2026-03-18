# Sinyal Stack

## Final MVP Stack

- Frontend: FastAPI-served marketing + app pages
- Auth: Supabase Auth
- Database: Supabase Postgres
- Payments: Mayar hosted checkout
- Billing sync: Mayar webhook -> backend -> Postgres

## Why This Stack

- Supabase Auth is faster to ship than custom auth and already solves sessions, password reset, and route protection.
- Postgres keeps users, subscriptions, payment logs, quotas, and saved searches in one place.
- Mayar is the fastest way to launch paid access in Indonesia without waiting on a longer gateway verification process.

## Payment Flow

1. User signs up with Supabase Auth.
2. Backend creates or updates `profiles`.
3. User picks a plan on `/payment`.
4. Frontend sends the user to a Mayar hosted checkout URL for that plan.
5. Mayar sends a payment webhook to the backend.
6. Backend writes `payment_transactions`.
7. Backend activates or updates `subscriptions`.
8. App checks subscription status and usage limits before serving paid features.

## Core Tables

- `profiles`
- `plans`
- `subscriptions`
- `payment_transactions`
- `usage_events`
- `saved_searches`
- `team_memberships`

## What Still Needs Wiring

- Supabase project keys and redirect URLs
- Mayar product URLs or checkout endpoints
- Webhook verification and idempotency
- Per-plan quota enforcement in app routes
