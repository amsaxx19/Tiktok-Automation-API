create extension if not exists pgcrypto;

create table if not exists public.profiles (
  user_id uuid primary key references auth.users(id) on delete cascade,
  email text unique,
  full_name text,
  company_name text,
  phone text,
  role text not null default 'owner',
  onboarding_use_case text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

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
  ('ringan', 'Paket Ringan', 'monthly', 59000, 30, 10, 10, 10, 1, 'Sinyal Paket Ringan'),
  ('tumbuh', 'Paket Tumbuh', 'monthly', 99000, 120, 40, 40, 40, 1, 'Sinyal Paket Tumbuh'),
  ('tim', 'Paket Tim', 'monthly', 299000, 500, 150, 150, 150, 3, 'Sinyal Paket Tim')
on conflict (code) do update
set
  name = excluded.name,
  billing_interval = excluded.billing_interval,
  price_idr = excluded.price_idr,
  monthly_search_limit = excluded.monthly_search_limit,
  monthly_profile_limit = excluded.monthly_profile_limit,
  monthly_comment_limit = excluded.monthly_comment_limit,
  monthly_transcript_limit = excluded.monthly_transcript_limit,
  seats = excluded.seats,
  mayar_product_name = excluded.mayar_product_name,
  active = true;

create table if not exists public.subscriptions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(user_id) on delete cascade,
  plan_code text not null references public.plans(code),
  provider text not null default 'mayar',
  provider_customer_id text,
  provider_subscription_id text,
  provider_invoice_id text,
  status text not null default 'pending' check (status in ('pending', 'active', 'past_due', 'expired', 'cancelled')),
  current_period_start timestamptz,
  current_period_end timestamptz,
  cancel_at_period_end boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists subscriptions_user_id_idx on public.subscriptions(user_id);
create index if not exists subscriptions_status_idx on public.subscriptions(status);

create table if not exists public.payment_transactions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references public.profiles(user_id) on delete set null,
  subscription_id uuid references public.subscriptions(id) on delete set null,
  provider text not null default 'mayar',
  provider_invoice_id text,
  provider_payment_id text,
  checkout_url text,
  amount_idr integer not null,
  currency text not null default 'IDR',
  status text not null default 'pending' check (status in ('pending', 'paid', 'failed', 'expired', 'refunded')),
  payer_email text,
  raw_payload jsonb not null default '{}'::jsonb,
  paid_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists payment_transactions_user_id_idx on public.payment_transactions(user_id);
create index if not exists payment_transactions_provider_invoice_id_idx on public.payment_transactions(provider_invoice_id);

create table if not exists public.usage_events (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(user_id) on delete cascade,
  event_type text not null check (event_type in ('search', 'profile', 'comments', 'transcript')),
  units integer not null default 1,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists usage_events_user_id_created_at_idx on public.usage_events(user_id, created_at desc);

create table if not exists public.saved_searches (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(user_id) on delete cascade,
  name text not null,
  query_text text not null,
  platforms text[] not null default '{}',
  filters jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists saved_searches_user_id_idx on public.saved_searches(user_id);

create table if not exists public.team_memberships (
  id uuid primary key default gen_random_uuid(),
  owner_user_id uuid not null references public.profiles(user_id) on delete cascade,
  member_user_id uuid not null references public.profiles(user_id) on delete cascade,
  role text not null default 'member',
  created_at timestamptz not null default now(),
  unique (owner_user_id, member_user_id)
);

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.profiles (user_id, email)
  values (new.id, new.email)
  on conflict (user_id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute procedure public.handle_new_user();
