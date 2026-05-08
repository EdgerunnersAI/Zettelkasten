-- iter-12 Task 7 / Class K4 — per-Kasten rolling top-1 frequency record.
-- Used by KastenStats in-memory cache + magnet-spotter bootstrap threshold.
create table if not exists kg_kasten_metrics (
  id bigserial primary key,
  sandbox_id uuid not null,
  top1_node_id text not null,
  ts timestamptz not null default now()
);

create index if not exists kg_kasten_metrics_sandbox_ts_idx
  on kg_kasten_metrics(sandbox_id, ts desc);
