-- Affiliate / referral system
-- Each user can become an affiliate with a unique referral code.
-- When a referred user makes a payment, the affiliate earns a commission.

create extension if not exists pgcrypto;

-- ── Affiliates ──
-- One row per user who activates their affiliate link.
create table if not exists public.affiliates (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null unique references auth.users(id) on delete cascade,
  referral_code text not null unique,
  commission_pct integer not null default 20,          -- e.g. 20 = 20%
  lifetime_earnings bigint not null default 0,         -- total earned (IDR)
  pending_balance bigint not null default 0,           -- available for payout
  paid_out bigint not null default 0,                  -- already withdrawn
  referral_count integer not null default 0,           -- total signups via code
  paid_referral_count integer not null default 0,      -- signups that converted
  is_active boolean not null default true,
  payout_method text,                                  -- 'bank_transfer', 'ewallet', etc.
  payout_detail jsonb not null default '{}'::jsonb,    -- { bank_name, account_number, account_name } or { ewallet, phone }
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists affiliates_referral_code_idx on public.affiliates(referral_code);
create index if not exists affiliates_user_id_idx on public.affiliates(user_id);

-- ── Affiliate Referrals ──
-- Tracks each signup that came through a referral link.
create table if not exists public.affiliate_referrals (
  id uuid primary key default gen_random_uuid(),
  affiliate_id uuid not null references public.affiliates(id) on delete cascade,
  referred_user_id uuid not null references auth.users(id) on delete cascade,
  referred_email text,
  status text not null default 'signed_up' check (status in ('signed_up', 'converted', 'cancelled')),
  converted_plan text,                                 -- plan code when they paid
  converted_amount bigint default 0,                   -- payment amount (IDR)
  commission_amount bigint default 0,                  -- commission earned from this referral
  signed_up_at timestamptz not null default now(),
  converted_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (affiliate_id, referred_user_id)
);

create index if not exists affiliate_referrals_affiliate_id_idx on public.affiliate_referrals(affiliate_id);
create index if not exists affiliate_referrals_referred_user_id_idx on public.affiliate_referrals(referred_user_id);

-- ── Affiliate Payouts ──
-- Payout request history.
create table if not exists public.affiliate_payouts (
  id uuid primary key default gen_random_uuid(),
  affiliate_id uuid not null references public.affiliates(id) on delete cascade,
  amount bigint not null,
  status text not null default 'pending' check (status in ('pending', 'processing', 'completed', 'rejected')),
  payout_method text,
  payout_detail jsonb not null default '{}'::jsonb,
  admin_note text,
  requested_at timestamptz not null default now(),
  processed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists affiliate_payouts_affiliate_id_idx on public.affiliate_payouts(affiliate_id);
create index if not exists affiliate_payouts_status_idx on public.affiliate_payouts(status);
