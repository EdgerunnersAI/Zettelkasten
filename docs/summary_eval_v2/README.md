# summary_eval_v2

Evaluation loop for the v2 summarization engine and v2 Supabase write/read path.

This folder intentionally mirrors the older `docs/summary_eval/{source}/iter-NN`
artifact shape while adding v2-only checks:

- route subtype and source-surface completeness metadata
- Supabase v2 write/read validation under the selected auth user
- UI graph visibility through `/api/graph?view=my`
- baseline comparison against the previous iteration or the final v1 loop

Use the scripts under `docs/summary_eval_v2/scripts/` from PowerShell.

```powershell
python docs/summary_eval_v2/scripts/baseline_score.py --source youtube --iter 1
python docs/summary_eval_v2/scripts/run_iter.py --source youtube --iter 1 --phase a --env dev
python docs/summary_eval_v2/scripts/post_iter.py --source youtube --iter 1
```

For live Naruto writes, use:

```powershell
python docs/summary_eval_v2/scripts/naruto_write.py --source newsletter --iter 1 --url "https://example.com/post" --user-id "<Naruto auth UUID>"
```

Never print secret values loaded from `.env`, `.env.v2`, `supabase/.env`, or
`new_envs.txt`.
