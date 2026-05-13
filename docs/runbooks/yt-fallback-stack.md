# YouTube Fallback Stack — Operator Setup

PR #14 added 3 new transcript tiers that depend on operator-provisioned external
resources. This runbook walks through each setup, ordered by ROI.

The tier chain (post-H3) is:

```
T1: tier_gemini_youtube_url               (server-side fetch — ~70% coverage; H1)
T2: tier_transcript_api_via_webshare      (region-locked-from-US captions; THIS RUNBOOK)
T3: tier_ytdlp_cookies_impersonate        (age-restricted + members-only + bot-gate; THIS RUNBOOK)
T4: tier_invidious_pool                   (IP diversification)
T5: tier_piped_pool                       (IP diversification)
T6: tier_gemini_audio                     (Gemini audio transcription)
T7: tier_metadata_only                    (low-confidence; H2 quality gate refuses)
```

Only T1 (Gemini fileData) consumes the 3-call LLM budget. T2 and T3 are
network-fetch tiers — no LLM cost.

---

## Tier-2 — Webshare free proxy ($0/mo)

1. Sign up at https://www.webshare.io/ (no card required).
2. Free tier: 10 proxies, 1 GB/mo, datacenter (per industry research this is
   datacenter not residential — still useful for transcript-api which is less
   aggressive than video stream).
3. Dashboard → "Proxy List" → copy the rotating-proxy URL
   (format: `http://user:pass@p.webshare.io:port`).
4. SSH droplet, edit `/opt/zettelkasten/compose/.env.local`:

   ```ini
   YT_TRANSCRIPT_PROXY_URL=http://user:pass@p.webshare.io:80
   YT_TRANSCRIPT_PROXY_USER=<user>
   YT_TRANSCRIPT_PROXY_PASS=<pass>
   ```

5. Restart compose:

   ```bash
   # droplet SSH
   docker compose -f /opt/zettelkasten/compose/docker-compose.yml restart zettelkasten-green
   ```

---

## Tier-3 — Burner Google + Firefox cookies + bgutil PO-token sidecar

### Step A — Burner Google account

- Create a new Google account (use protonmail / SimpleLogin alias). Age-verify it.
- DO NOT use a real account — YouTube can ban the account that fed the cookie.

### Step B — Firefox cookie export (operator's laptop)

- Install Firefox, open private window, log into the burner account.
- Watch one age-restricted video to seed the session.
- Use the Cookie Editor extension, or `yt-dlp --cookies-from-browser firefox`
  if running locally.
- Export to `cookies.txt` (Netscape format).
- scp to droplet:

  ```bash
  # operator laptop (Git Bash / PowerShell)
  scp -i ~/.ssh/zettelkasten_deploy cookies.txt deploy@167.71.235.58:/opt/zk/yt-cookies.txt
  ssh -i ~/.ssh/zettelkasten_deploy deploy@167.71.235.58 \
    'sudo chmod 600 /opt/zk/yt-cookies.txt && sudo chown root:root /opt/zk/yt-cookies.txt'
  ```

- Calendar reminder: re-export every 10 days (cookies expire / get invalidated
  when used from datacenter IPs).

### Step C — bgutil PO-token sidecar

- `bgutil-ytdlp-pot-provider` runs as a Node.js HTTP service on port 4416.
- Install on droplet:

  ```bash
  # droplet SSH
  ssh -i ~/.ssh/zettelkasten_deploy deploy@167.71.235.58
  sudo apt-get update && sudo apt-get install -y nodejs npm
  sudo npm install -g bgutil-ytdlp-pot-provider
  # Create systemd unit
  sudo tee /etc/systemd/system/bgutil-pot.service <<EOF
  [Unit]
  Description=bgutil PO Token Provider for yt-dlp
  After=network-online.target

  [Service]
  ExecStart=/usr/bin/bgutil-pot-provider --port 4416 --host 127.0.0.1
  Restart=on-failure
  RestartSec=10
  CPUWeight=80
  MemoryMax=200M
  User=deploy

  [Install]
  WantedBy=multi-user.target
  EOF
  sudo systemctl daemon-reload
  sudo systemctl enable --now bgutil-pot
  ```

- Verify:

  ```bash
  # droplet SSH
  curl -s http://127.0.0.1:4416/healthz
  ```

- Memory budget: ~80-150MB. Fits in our 2GB droplet headroom alongside future
  WARP+Tor (see ./d8-droplet-overhead.md).

---

## Verification

After setting up all three:

```bash
# droplet SSH
ssh -i ~/.ssh/zettelkasten_deploy deploy@167.71.235.58 'docker exec zettelkasten-green python -c "
import os, asyncio
from website.features.summarization_engine.source_ingest.youtube.tiers import (
    tier_transcript_api_via_webshare,
    tier_ytdlp_cookies_impersonate,
)
config = {\"transcript_languages\": [\"en\"], \"ytdlp_player_clients\": [\"tv_simply\",\"android_sdkless\",\"ios\",\"web_safari\",\"web\"]}
r2 = asyncio.run(tier_transcript_api_via_webshare(\"O7FIiYsVy3U\", config))
print(\"T2:\", r2.success, r2.error or r2.transcript[:80])
r3 = asyncio.run(tier_ytdlp_cookies_impersonate(\"O7FIiYsVy3U\", config))
print(\"T3:\", r3.success, r3.error or r3.transcript[:80])
"'
```

---

## Failure & rotation

- Webshare proxy outage → T2 returns `IpBlocked` → falls to T3.
- Cookies expired → T3 returns "Sign in to confirm" → falls to T4 Invidious.
- bgutil sidecar down → T3 still attempts but most clients fail without
  PO-token; falls through fast.
- All tiers failing → T7 metadata_only → H2 quality gate refuses with HTTP 422
  if raw_text < 500 chars.


## Preflight refusal (H4/T7)

Some YouTube URLs are known-bad up-front and should never enter the tier
chain — running the chain (or worse, a Gemini call) just wastes LLM budget
and risks hallucination. The orchestrator runs a cheap `yt-dlp --simulate
--dump-single-json` preflight on every YouTube URL **before** the tier chain
and short-circuits with `UnsupportedVideoError`, which the route handler
translates to HTTP 422.

### URLs that short-circuit at preflight

| `detail.reason`                       | Trigger                                                  |
|---------------------------------------|----------------------------------------------------------|
| `private`                             | `availability == "private"` or "Private video" error     |
| `removed_or_unavailable`              | "video removed" / "no longer available" / "not available"|
| `active_livestream`                   | `is_live == True`                                        |
| `premiere_or_post_live`               | `live_status in {is_upcoming, post_live}`                |
| `premiere_or_live`                    | "premiere" / "scheduled" / "live event" in error         |
| `members_only_or_age_restricted`     | `availability == "needs_auth"`                           |

### HTTP 422 response shape

```json
{
  "detail": {
    "error": "unsupported_video_type",
    "reason": "private",
    "confidence": "insufficient",
    "confidence_reason": "Video type cannot be ingested: private",
    "quality_signals": {"input_chars": 0, "source_tier": "preflight_refused"}
  }
}
```

Distinct from H2's other 422: that one fires AFTER the chain runs and lands
on `metadata_only` with <500 chars (`detail.error == "insufficient_context"`,
`source_tier == "metadata_only"`). H4's 422 fires BEFORE the chain runs
(`source_tier == "preflight_refused"`, `input_chars == 0`).

### Reason-code interpretation

- `private`, `removed_or_unavailable` → terminal; the URL will never work.
- `active_livestream`, `premiere_or_post_live`, `premiere_or_live` →
  retryable once the stream ends or the premiere airs.
- `members_only_or_age_restricted` → would need authenticated cookies in T3;
  currently rejected because the public chain cannot satisfy it.

### Why preflight saves budget

A private video that reaches T1 (`gemini_youtube_url`) burns a Gemini call
on `Part.from_uri(...)` only to get `INVALID_ARGUMENT: file must be public`.
A livestream that reaches T6 (`gemini_audio`) burns an even larger audio
upload only for "no audio stream". Preflight skips all of that with a single
local yt-dlp metadata dump — zero LLM tokens, zero proxy traffic.

### What does NOT trigger preflight refusal

Bot-detection (`Sign in to confirm you're not a bot`) and other transient
yt-dlp errors fall through. The tier chain (cookies + curl_cffi impersonate
+ PO-token in T3) is designed to handle those, so preflight must not steal
those URLs from it.
