-- ============================================================
-- SINYAL – Full Database Setup Script
-- Paste this entire file into Supabase → SQL Editor → Run
-- (Safe to run multiple times – uses IF NOT EXISTS / ON CONFLICT)
--
-- NOTE: profiles table already exists with `id uuid` as PK.
-- All FK references use profiles(id).
-- ============================================================

-- ── 0. Extensions ──────────────────────────────────────────
create extension if not exists pgcrypto;

-- ── 1. profiles – add missing columns if not already present ──
alter table public.profiles
  add column if not exists tier text not null default 'free',
  add column if not exists daily_searches_left integer not null default 3,
  add column if not exists last_search_reset text not null default '',
  add column if not exists monthly_profiles_used integer not null default 0,
  add column if not exists monthly_comments_used integer not null default 0,
  add column if not exists monthly_reset_date text not null default '';

-- ── 2. plans ───────────────────────────────────────────────
create table if not exists public.plans (
  code text primary key,
  name text not null,
  billing_interval text not null check (billing_interval in ('monthly', 'yearly', 'lifetime')),
  price_idr integer not null,
  monthly_search_limit integer not null default 0,
  monthly_profile_limit integer not null default 0,
  monthly_comment_limit integer not null default 0,
  monthly_transcript_limit integer not null default 0,
  seats integer not null default 1,
  mayar_product_url text,
  mayar_product_name text,
  active boolean not null default true,
  created_at timestamptz not null default now()
);

delete from public.plans where code in ('ringan', 'tumbuh', 'tim');

insert into public.plans (
  code, name, billing_interval, price_idr,
  monthly_search_limit, monthly_profile_limit,
  monthly_comment_limit, monthly_transcript_limit,
  seats, mayar_product_name
) values
  ('starter',  'Starter',       'monthly',  49000,  30,  20,  20,  10, 1, 'Sinyal Starter'),
  ('pro',      'Pro',           'monthly',  99000,   0,   0,   0,   0, 1, 'Sinyal Pro'),
  ('lifetime', 'Lifetime Deal', 'lifetime', 299000,  0,   0,   0,   0, 1, 'Sinyal Lifetime Deal')
on conflict (code) do update set
  name                     = excluded.name,
  billing_interval         = excluded.billing_interval,
  price_idr                = excluded.price_idr,
  monthly_search_limit     = excluded.monthly_search_limit,
  monthly_profile_limit    = excluded.monthly_profile_limit,
  monthly_comment_limit    = excluded.monthly_comment_limit,
  monthly_transcript_limit = excluded.monthly_transcript_limit,
  seats                    = excluded.seats,
  mayar_product_name       = excluded.mayar_product_name,
  active                   = true;

-- ── 3. subscriptions ───────────────────────────────────────
create table if not exists public.subscriptions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  plan_code text not null references public.plans(code),
  provider text not null default 'mayar',
  provider_customer_id text,
  provider_subscription_id text,
  provider_invoice_id text,
  status text not null default 'pending'
    check (status in ('pending', 'active', 'past_due', 'expired', 'cancelled')),
  current_period_start timestamptz,
  current_period_end timestamptz,
  cancel_at_period_end boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists subscriptions_user_id_idx on public.subscriptions(user_id);
create index if not exists subscriptions_status_idx  on public.subscriptions(status);

-- ── 4. payment_transactions ────────────────────────────────
create table if not exists public.payment_transactions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references public.profiles(id) on delete set null,
  subscription_id uuid references public.subscriptions(id) on delete set null,
  provider text not null default 'mayar',
  provider_invoice_id text,
  provider_payment_id text,
  checkout_url text,
  amount_idr integer not null,
  currency text not null default 'IDR',
  status text not null default 'pending'
    check (status in ('pending', 'paid', 'failed', 'expired', 'refunded')),
  payer_email text,
  raw_payload jsonb not null default '{}'::jsonb,
  paid_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists payment_transactions_user_id_idx
  on public.payment_transactions(user_id);
create index if not exists payment_transactions_provider_invoice_id_idx
  on public.payment_transactions(provider_invoice_id);

do $$ begin
  if not exists (
    select 1 from pg_constraint
    where conname = 'payment_transactions_provider_invoice_uniq'
  ) then
    alter table public.payment_transactions
      add constraint payment_transactions_provider_invoice_uniq
        unique (provider, provider_invoice_id);
  end if;
end $$;

-- ── 5. usage_events ────────────────────────────────────────
create table if not exists public.usage_events (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  event_type text not null
    check (event_type in ('search', 'profile', 'comments', 'transcript')),
  units integer not null default 1,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists usage_events_user_id_created_at_idx
  on public.usage_events(user_id, created_at desc);

-- ── 6. saved_searches ──────────────────────────────────────
create table if not exists public.saved_searches (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  name text not null,
  query_text text not null,
  platforms text[] not null default '{}',
  filters jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists saved_searches_user_id_idx on public.saved_searches(user_id);

-- ── 7. team_memberships ────────────────────────────────────
create table if not exists public.team_memberships (
  id uuid primary key default gen_random_uuid(),
  owner_user_id uuid not null references public.profiles(id) on delete cascade,
  member_user_id uuid not null references public.profiles(id) on delete cascade,
  role text not null default 'member',
  created_at timestamptz not null default now(),
  unique (owner_user_id, member_user_id)
);

-- ── 8. Auto-create profile on signup ───────────────────────
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.profiles (id, email)
  values (new.id, new.email)
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute procedure public.handle_new_user();
