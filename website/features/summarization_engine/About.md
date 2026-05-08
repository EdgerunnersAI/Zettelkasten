# Summarization Engine v2

Pure-library summarization engine that ingests URLs from 9 content sources and produces structured Zettelkasten summaries via tiered Gemini 2.5 Pro + Flash.

## Public API
- `summarize_url(url, user_id)` - single URL, real-time
- `BatchProcessor(user_id).run(input_path | input_bytes)` - CSV/JSON batch
- Single writer: `SupabaseWriter` (writes the structured summary into `kg_nodes.summary`).

## Integration
- `/api/v2/summarize` and `/api/v2/batch*` endpoints alongside existing `/api/summarize`
- Reuses `website/features/api_key_switching/key_pool.py`

See `docs/superpowers/specs/2026-04-10-summarization-engine-v2-design.md` for full design.
