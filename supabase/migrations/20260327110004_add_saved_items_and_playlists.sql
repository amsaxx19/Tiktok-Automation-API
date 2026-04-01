-- Save list / playlist feature
-- Safe to run multiple times.

create extension if not exists pgcrypto;

create table if not exists public.saved_playlists (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  name text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (user_id, name)
);

create index if not exists saved_playlists_user_id_idx
  on public.saved_playlists(user_id);

create table if not exists public.saved_items (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  playlist_id uuid references public.saved_playlists(id) on delete set null,
  platform text,
  author text,
  title text,
  caption text,
  transcript text,
  video_url text,
  thumbnail text,
  views bigint,
  likes bigint,
  comments bigint,
  shares bigint,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists saved_items_user_id_created_at_idx
  on public.saved_items(user_id, created_at desc);

create index if not exists saved_items_playlist_id_idx
  on public.saved_items(playlist_id);

create index if not exists saved_items_video_url_idx
  on public.saved_items(video_url);
