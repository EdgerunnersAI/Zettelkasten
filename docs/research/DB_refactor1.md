<img src="https://r2cdn.perplexity.ai/pplx-full-logo-primary-dark%402x.png" style="height:64px;margin-right:32px"/>

# Continue

You already have the right modular split in `supabase/website` (KG, RAG, summarization, auth, pricing), but the schemas are feature‑centric rather than “user‑/workspace‑centric”, and usage/metrics are scattered across modules. The redesign I’d recommend is to introduce a small set of **core multi‑tenant tables** (users, workspaces, zettels, documents, chunks, nodes, edges, usage) that all your existing feature schemas plug into, instead of each module owning its own notion of users, content, and usage.[^1]

Below is a concrete, Supabase‑friendly redesign that meets your 6 targets.

***

## What your current Supabase layout looks like

From the `supabase/website` folder in `Zettelkasten_KG` you currently have these modules:

- `kg_public/` – core KG schema plus a long chain of migrations for enums, node columns, usage edges, bandit/anchor seeds, enriched metadata, kasten frequencies, link relation enums, RAG anchors, chunk counts, etc.
- `kg_features/` – feature/analytics‑oriented KG tables (`001_intelligence.sql`, `002_global_scale.sql`, `003_scale_storage_optimizations.sql`).
- `rag_chatbot/` – vector search infra + RAG plumbing: HNSW migration, `chunks` table, `sandboxes`, `chat_sessions`, RAG RPCs and fixes.
- `summarization_engine/` – summarization engine schema (`001_engine_v2.sql`).
- `nexus/` – a `schema.sql` for your “nexus” orchestration layer.
- `user_auth/` – user auth/profile schema on top of Supabase auth.[^1]
- `user_pricing/` – plans, pricing and usage/limits schema.[^1]

From `SKILL.md` (which mirrors the Supabase `agent-skills` repo), the intent is: **multi‑tenant, workspace‑based** app where RAG, KG, summarization and pricing all hang off shared identity/usage primitives, not isolated schemas.[^1]

***

## Key gaps vs your goals

### 1. Multi‑tenant modeling is implicit, not explicit

- Many KG and RAG tables are feature‑centric (“anchor seeds”, “dense recall”, “usage edges”) and likely only carry `user_id`/`profile_id` or nothing at all, rather than a first‑class **workspace/tenant key** everywhere.[^1]
- Public KG (“world knowledge”) versus personal KG (user’s Zettelkasten) is not cleanly separated – it’s easy to end up with user‑specific facts inside `kg_public` tables that were intended to be global.[^1]


### 2. RAG vs KG vs Summarization duplication

- You effectively have three parallel “views” over content: KG nodes/edges (`kg_public`), RAG chunks and sandboxes (`rag_chatbot`), and summarization tasks (`summarization_engine`), each with its own tables and sometimes overlapping metadata.[^1]
- Zettel content, extracted entities, chunks, and summaries are not modeled around a common **Document / Chunk / Node** core – which is what leads to duplication when the same Zettel flows through KG extraction, RAG, and summarization.[^1]


### 3. Usage \& pricing fragmented across modules

- You already have `kg_usage_edges`, `kasten_metrics`, chat message verdicts, and a rich `user_pricing` schema, but usage accounting is spread out across migrations in `kg_public` and RAG tables.[^1]
- This makes it hard to answer: “How much did this workspace/user consume across *all* features this billing period?” without complex cross‑schema aggregation.


### 4. Zettel sharing \& deduplication not represented

- The Supabase schemas don’t yet expose a clear **canonical Zettel** model plus “user/workspace overlays” and a sharing layer, which is what you’ll need to avoid duplicating Zettel rows across users when sharing.[^1]
- Current schemas are more about infra (KG, RAG, pricing) than about Zettels as first‑class resources that can be shared, versioned and embedded into KG/RAG.

***

## High‑level redesign: core versus feature schemas

The key change is to introduce a **core schema** (call it `app_core`) that owns users, workspaces, Zettels, documents, chunks, nodes, edges and usage; and let each feature schema (`kg_public`, `rag_chatbot`, `summarization_engine`, `user_pricing`, etc.) reference those core tables instead of defining their own “content/user” records.[^1]

### Core identity and tenancy

Use Supabase auth as the root of identity and add these in `app_core`:[^1]

- `profiles`
    - `id uuid primary key references auth.users(id)`
    - `display_name text`, `avatar_url text`, `created_at timestamptz`, …
- `workspaces` (your “vaults” or “zettel spaces”)
    - `id uuid primary key`
    - `owner_profile_id uuid references app_core.profiles(id)`
    - `name text`, `is_personal boolean`, `created_at`
- `workspace_members`
    - `workspace_id uuid references app_core.workspaces(id)`
    - `profile_id uuid references app_core.profiles(id)`
    - `role text check in ('owner','editor','viewer',...)`
    - PK `(workspace_id, profile_id)`

Row‑level security: every KG/RAG/Zettel table should carry `workspace_id` and use RLS policies tied to `workspace_members` so you can scale to 10k users safely while keeping queries indexed on `(workspace_id, ...)`.[^1]

***

## Zettels, documents, and chunks (dedup foundation)

Instead of each module inventing its own idea of “content”, centralize it:

- `zettels`
    - `id uuid primary key`
    - `workspace_id uuid references app_core.workspaces(id)`
    - `canonical_zettel_id uuid null references app_core.zettels(id)`  — for shared/forked zettels (explained below)
    - `title text`, `body_md text`, `created_by_profile_id uuid`, `created_at`, `updated_at`
- `zettel_versions`
    - `id bigserial primary key`
    - `zettel_id uuid references app_core.zettels(id)`
    - `version_no int`, `body_md text`, `summary text`, `created_at`
    - Optional: store embeddings for the whole Zettel here.
- `documents` (generic ingestion unit; Zettels are one kind of document)
    - `id uuid primary key`
    - `workspace_id uuid references app_core.workspaces(id)`
    - `zettel_id uuid null references app_core.zettels(id)`
    - `source_type enum('zettel','file','url','note',...)`
    - `source_uri text`, `raw_text text`, `metadata jsonb`, `created_at`
- `chunks` (RAG chunks, used by chatbot \& search)
    - `id uuid primary key`
    - `document_id uuid references app_core.documents(id)`
    - `workspace_id uuid not null` (denormalized for fast filtering)
    - `chunk_index int`, `text text`, `embedding vector`, `metadata jsonb`
    - HNSW/GiST index on `embedding`, B‑tree on `(workspace_id, document_id, chunk_index)`.[^1]

Your existing `rag_chatbot` scripts (`002_chunks_table.sql`, `003_sandboxes.sql`, `004_chat_sessions.sql`, `005_rag_rpcs.sql`, etc.) can be refactored to **reference `app_core.chunks`** instead of owning their own chunks table, and to use `workspace_id` consistently.[^1]

***

## Knowledge graph normalization

You already have a rich KG: nodes, edges, link relations, blocklists, node aliases, usage edges, etc., defined in `kg_public/schema.sql` and its migrations. I’d normalize it around these core tables in `kg_public` (or move them to `app_core_kg` and alias them):[^1]

- `kg_nodes`
    - `id bigserial primary key`
    - `workspace_id uuid null` — null = global/public node; non‑null = workspace‑scoped node
    - `type text` (entity / concept / tag / person / org / topic)
    - `canonical_name text`, `slug text unique`, `metadata jsonb`, `created_at`
- `kg_edges`
    - `id bigserial primary key`
    - `workspace_id uuid null`
    - `src_node_id bigint references kg_nodes(id)`
    - `dst_node_id bigint references kg_nodes(id)`
    - `relation_type text` constrained by your `kg_link_relation_enum` migration.
    - `weight numeric`, `evidence_document_id uuid references app_core.documents(id)`, `metadata jsonb`

Bridge tables to connect KG back to Zettels and chunks:

- `zettel_nodes`
    - `zettel_id uuid references app_core.zettels(id)`
    - `node_id bigint references kg_nodes(id)`
    - `relationship text` (tagged_as, mentions, defines, etc.)
    - PK `(zettel_id, node_id, relationship)`
- `chunk_nodes`
    - `chunk_id uuid references app_core.chunks(id)`
    - `node_id bigint references kg_nodes(id)`
    - `relationship text`, `score numeric`
    - Replaces the need for multiple RAG‑specific “entity anchor” tables like `rag_entity_anchor`, `rag_kasten_chunk_counts` — they become **views** over this base table filtered by `source_type` or `relationship`.[^1]

This way:

- A Zettel’s entities/links are extracted into `zettel_nodes` \& `kg_edges`.
- RAG chunks map to nodes via `chunk_nodes`.
- RAG, KG, summarization all share the same underlying entities and evidence documents, eliminating duplication of “entity lists” per module.

***

## Unified usage, metrics, and pricing

Right now you have: `kg_usage_edges`, `kasten_metrics`, chat message verdict constraints, and a fairly detailed `user_pricing` schema + migrations for pricing/usage audit columns. I’d consolidate this into two layers:[^1]

### Core usage events (in `app_core_billing`)

- `usage_events`
    - `id bigserial primary key`
    - `workspace_id uuid`
    - `profile_id uuid`
    - `feature text` (e.g. 'kg_extract', 'kg_chat', 'rag_chat', 'summarize', 'import_zettel')
    - `quantity numeric` (tokens, nodes, messages, chunks, etc.)
    - `unit text` ('tokens','messages','nodes','calls', etc.)
    - `metadata jsonb` (e.g. {chat_session_id, node_id, plan_id})
    - `occurred_at timestamptz`
- `usage_aggregates` (materialized view or table maintained incrementally)
    - `(workspace_id, billing_period_start, feature) -> sum(quantity)`

All feature schemas log into `usage_events` instead of their own counters; `kg_usage_edges` and `kasten_metrics` can be simplified to views or summary tables over `usage_events` scoped to KG‑related features.[^1]

### Plans, entitlements, and limits (in `user_pricing`)

Adapt your existing `user_pricing/schema.sql` and `2026-05-01_user_pricing.sql` migration to hang off the above usage primitives:[^1]

- `plans` — definitions of plans and quotas per feature.
- `workspace_plans` — which plan a workspace is on, with effective dates.
- `plan_entitlements` — for each plan \& feature, allowable `unit` and `monthly_limit`.
- The billing logic then becomes: sum `usage_events` per workspace/feature/unit, compare to entitlements, and enforce quotas in app logic or via RLS helper functions.

This satisfies your “scale to 10k users tomorrow” constraint because:

- All hot paths are `workspace_id`‑partitioned and indexable.
- Usage writes are append‑only in a single table, easy to batch summarize.
- You can add/remove features or change metering (e.g. shift a KG feature from “nodes” to “tokens”) without schema surgery — just update entitlements and logging metadata.

***

## Zettel sharing without duplication

To allow sharing of a Zettel between users/workspaces without duplicating its full content, you can use a **canonical zettel + overlay** pattern:

- `canonical_zettels`
    - `id uuid primary key`
    - `owner_workspace_id uuid`
    - `title text`, `body_md text`, `created_at`, `updated_at`
- `workspace_zettels`
    - `id uuid primary key`
    - `workspace_id uuid references app_core.workspaces(id)`
    - `canonical_zettel_id uuid references canonical_zettels(id)`
    - `access_level enum('owner','editor','viewer')`
    - `pinned boolean`, `local_metadata jsonb` (user’s tags, folders, etc.)
- Optional: `zettel_overlays` (for user‑specific edits that don’t overwrite the canonical)
    - `workspace_zettel_id uuid references workspace_zettels(id)`
    - `body_md text`, `version_no`, `created_at`

When a user shares a Zettel with another:

- You *do not* copy its text; you just create another `workspace_zettels` row pointing to the same `canonical_zettel_id`.
- KG extractions and RAG chunks should be attached to the canonical Zettel’s `documents` and `chunks`, so all consumers reuse the same embeddings and KG nodes.
- Per‑user settings (pinned, last_read_at, color, notebooks) stay in `workspace_zettels.local_metadata`.

This directly addresses your deduplication target for sharing (no duplicate Zettel content, no duplicate embeddings/KG nodes), while still preserving per‑user affordances.

***

## Making schemas dynamic and future‑proof

To keep things adaptable when you “increase coverage in some modules and reduce it in others”, favor configuration tables + metadata over hard‑coding everything into separate tables.[^1]

Examples:

- **Retrieval strategies** instead of hard‑wired bandit tables
    - `retrieval_configs` (workspace‑scoped, feature‑scoped) with JSONB parameters; your existing `anchor_seed_bandit`, `rag_dense_recall` migrations can log into generic `retrieval_events` and `bandit_arms` tables keyed by `retrieval_config_id` instead of separate tables per experiment.[^1]
- **Task pipeline definitions** for summarization and KG extraction
    - `pipelines` (name, type='summarization'|'kg_extract'|'rag_ingest', config jsonb)
    - `pipeline_runs` (pipeline_id, workspace_id, input_document_id, status, metrics jsonb).
    - Your current `summarization_engine` `engine_v2` schema then becomes a specific pipeline definition plus a few supporting tables.[^1]

This way, adding/removing modules is often:

- Add a row in a config table (new pipeline, new retrieval strategy, new priced feature).
- Add or remove a couple of feature‑specific log tables, without touching core entities (users, workspaces, zettels, documents, chunks, nodes, usage).

***

## Supabase‑specific implementation notes

Based on the `agent-skills` patterns described in SKILL.md and mirrored in your repo:[^1]

- Keep **auth at the edge**: rely on `auth.users` and `app_core.profiles`; avoid duplicating auth in each schema.
- Use **Postgres schemas as feature boundaries** (you already do) but enforce **foreign keys into `app_core`** everywhere instead of per‑feature user/content tables.
- Add `workspace_id` to all hot tables (`chunks`, `kg_nodes`, `kg_edges`, `chat_sessions`, `summaries`, `usage_events`) with indexes on `(workspace_id, ...)`.
- Centralize **RLS policies**: define helper functions like `current_workspace_ids()` and reuse them across schemas to guarantee consistent multi‑tenant isolation.
- For scale: partition huge append‑only tables (`usage_events`, `chat_messages`, possibly `kg_edges`) by time and/or `workspace_id`, while keeping lookup tables (plans, nodes, zettels) regular.

***

## Concrete next steps for your repo

Given the current SQL files and migrations you have under `supabase/website`, here is a practical refactor path that doesn’t nuke everything:[^1]

1. **Introduce `app_core` schema**
    - Add `profiles`, `workspaces`, `workspace_members`, `zettels`, `documents`, `chunks`, `kg_nodes`, `kg_edges`, `usage_events` as described.
    - Wire RLS and indexes around `workspace_id`.
2. **Refit `rag_chatbot` to core chunks**
    - Migrate `rag_chatbot` to reference `app_core.chunks` instead of a local chunks table; keep `chat_sessions`, `sandboxes`, RPCs as‑is but join via `workspace_id` and `chunk_id`.
3. **Refit `kg_public` to core KG**
    - Collapse any duplicate “entity/anchor” tables into `kg_nodes`, `kg_edges`, `chunk_nodes`, `zettel_nodes`, with views preserving your current API surface.
    - Ensure global vs workspace KG is governed by `workspace_id` nullability and RLS.
4. **Centralize usage \& pricing**
    - Have KG and RAG code paths emit rows into `usage_events`.
    - Keep your `user_pricing` schema but refactor it to compute from `usage_events` instead of bespoke counters.
5. **Add Zettel sharing layer**
    - Create `canonical_zettels` + `workspace_zettels` and migrate current Zettel storage (wherever it lives now) into this model.
    - Update your ingestion/processing jobs so Zettels always go through `documents` → `chunks` → KG extraction with shared entities.
6. **Gradually deprecate old per‑feature tables**
    - For each feature schema, keep a view or compatibility RPC that presents the same interface but backed by the new core tables.
    - Once you’ve updated application code, drop the old tables.

If you’d like, next step I can do is sketch out **actual Postgres/Supabase DDL** (concrete `CREATE TABLE` statements and RLS policies) for `app_core` and show where your existing `supabase/website/*/*.sql` files would be simplified or replaced.

<div align="center">⁂</div>

[^1]: SKILL.md

