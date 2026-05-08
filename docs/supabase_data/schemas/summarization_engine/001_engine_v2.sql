-- Summarization Engine v2 metadata and batch tracking.

alter table if exists public.kg_nodes
  add column if not exists summary_v2 jsonb,
  add column if not exists extraction_confidence text,
  add column if not exists engine_version text;

create table if not exists public.summary_batch_runs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.kg_users(id) on delete cascade,
  status text not null default 'pending',
  input_filename text,
  input_format text,
  mode text not null default 'realtime',
  total_urls integer not null default 0,
  processed_count integer not null default 0,
  success_count integer not null default 0,
  skipped_count integer not null default 0,
  failed_count integer not null default 0,
  error_message text,
  config_snapshot jsonb not null default '{}'::jsonb,
  started_at timestamptz not null default now(),
  completed_at timestamptz
);

create table if not exists public.summary_batch_items (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references public.summary_batch_runs(id) on delete cascade,
  user_id uuid not null references public.kg_users(id) on delete cascade,
  url text not null,
  source_type text,
  status text not null default 'pending',
  node_id text,
  error_code text,
  error_message text,
  tokens_used integer,
  latency_ms integer,
  user_tags text[] not null default '{}',
  user_note text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists summary_batch_items_run_id_idx
  on public.summary_batch_items(run_id);

create index if not exists summary_batch_runs_user_id_idx
  on public.summary_batch_runs(user_id);
