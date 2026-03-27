-- Add monthly usage counters used by backend quota enforcement.
-- Safe to run multiple times.

alter table public.profiles
    add column if not exists monthly_profiles_used integer not null default 0,
    add column if not exists monthly_comments_used integer not null default 0,
    add column if not exists monthly_reset_date text not null default '';
