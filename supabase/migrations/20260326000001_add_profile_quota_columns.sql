-- Add quota and billing tier columns to profiles.
-- These columns are required by the quota enforcement logic in server.py.

alter table public.profiles
    add column if not exists tier text not null default 'free'
        check (tier in ('free', 'starter', 'pro', 'lifetime')),
    add column if not exists daily_searches_left integer not null default 3,
    add column if not exists last_search_reset text not null default '';

-- Sync plan definitions to match server.py PLAN_CATALOG (starter / pro / lifetime).
-- The initial migration used ringan/tumbuh/tim – replace with the correct codes.
delete from public.plans where code in ('ringan', 'tumbuh', 'tim');

insert into public.plans (
    code,
    name,
    billing_interval,
    price_idr,
    monthly_search_limit,
    monthly_profile_limit,
    monthly_comment_limit,
    monthly_transcript_limit,
    seats,
    mayar_product_name
)
values
    ('starter',  'Starter',      'monthly',  49000,  30,  20,  20,  10, 1, 'Sinyal Starter'),
    ('pro',      'Pro',          'monthly',  99000,   0,   0,   0,   0, 1, 'Sinyal Pro'),
    ('lifetime', 'Lifetime Deal','lifetime', 299000,  0,   0,   0,   0, 1, 'Sinyal Lifetime Deal')
on conflict (code) do update
set
    name                   = excluded.name,
    billing_interval       = excluded.billing_interval,
    price_idr              = excluded.price_idr,
    monthly_search_limit   = excluded.monthly_search_limit,
    monthly_profile_limit  = excluded.monthly_profile_limit,
    monthly_comment_limit  = excluded.monthly_comment_limit,
    monthly_transcript_limit = excluded.monthly_transcript_limit,
    seats                  = excluded.seats,
    mayar_product_name     = excluded.mayar_product_name,
    active                 = true;
