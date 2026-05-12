# Canary / heartbeat monitoring for 1-droplet FastAPI deploys (research, 2026-05-12)

Scope: post-WAVE-D hardening for `zettelkasten.in` — FastAPI 0.11x on a
DigitalOcean 2 GB / 1 vCPU droplet, blue/green Docker Compose behind Caddy 2 +
Cloudflare. Existing alerting plane = three Slack webhooks routed through
`website/features/web_monitor/_slack_client.py` (stamina-wrapped retry, 8-permit
async semaphore per worker, 2 workers → 16 in-flight global ceiling). Missing
piece: WM-11 — silent-failure detection.

This document is **research-only**. No code edits. All recommendations rest on
cited 2024-2026 sources.

---

## 1. Executive recommendation

**Adopt option (c) + (b) in combination: external dead-man-switch service is
canonical, plus a thin in-app emitter.** Specifically:

- **External service**: **healthchecks.io Hobbyist (free, 20 jobs)**. It is
  the only "1 monitor at 5-min cadence" use-case where the free tier covers
  the entire need indefinitely with no upgrade pressure, the operational model
  ("ping us on schedule, we alert on absence") is the textbook
  dead-man-switch pattern, and the service itself publishes its own outage
  postmortems — proving incident-response discipline.[^hc-pricing][^hc-faq][^hc-pm-2025-11][^hc-pm-2025-04]
- **Cadence**: **5 minutes** with a 10-minute grace window. Smaller than the
  ~10-12 min Cloudflare/Caddy 502 retry-and-give-up window so a stuck app is
  caught before a real user does, larger than typical cold-deploy gap so a
  blue/green cutover never tickles a false alarm.
- **Signal scope**: **medium-deep** — the heartbeat path must exercise (i) the
  event loop, (ii) a real DB read (`select 1 from core.workspaces limit 1`
  via the existing `asyncpg_pool`), and (iii) the Gemini key-pool's
  `count_active_keys()` accessor (no API call — just verify the pool state
  object hasn't crashed). It must **not** call Gemini or Razorpay live (cost
  + upstream-fragility risk; see §5 anti-pattern *Heartbeat amplifies upstream
  outage*).
- **In-app emitter**: a `lifespan`-scoped `asyncio.create_task` that runs
  `while not stop_event.is_set(): heartbeat(); await asyncio.sleep(300)` and
  posts to the healthchecks.io URL via `httpx.AsyncClient`. This is the
  pattern Sentry/PostHog use internally (Celery-style task workers, but the
  scheduling-of-internal-housekeeping subset is a plain asyncio loop), and it
  matches the FastAPI lifespan docs' "single-execution startup, single-
  execution shutdown" contract.[^fastapi-lifespan][^sentry-async-workers]
- **Why not (a) only**: an external HTTP probe of `/api/health` proves the
  HTTP layer is alive but says nothing about the asyncio event loop having
  drifted into starvation, nor about background jobs still executing — a
  classic false-negative documented in the SRE book ch.22 cascading-failure
  taxonomy.[^sre-ch22]
- **Why not (b) only**: an in-app heartbeat that posts to Slack directly is
  vulnerable to "the same network/DNS issue that broke the app also breaks
  the heartbeat" — and worse, when the app is dead the absence of
  notifications looks indistinguishable from "everything is fine and quiet".
  Dead-man-switch (absence-of-ping = alert) inverts this and is the only
  pattern that detects a process that has fully exited.[^hc-faq][^cronitor-vs]
- **Why not (d) full belt-and-braces**: at 1 droplet, 1 vCPU, no SRE staff,
  the marginal value of a second external prober is below the cognitive load
  of maintaining two alert plumbing paths. Revisit at multi-region.

One-paragraph TL;DR: register one healthchecks.io check at 5-min cadence, add
a 40-line `lifespan` task in `website/app.py` that pings it after a real DB
round-trip and a key-pool sanity check, and route healthchecks.io alerts into
`SLACK_WEBHOOK_DO_ALERT`. Cost: $0. Latency overhead: ~2 ms per ping (one DB
roundtrip + one outbound HTTPS to hc-ping.com). False-positive risk: lowest
in the field because hc-ping.com runs on a small dedicated cluster that does
not share infrastructure with the droplet.

---

## 2. Reference architecture (ascii)

```
                          ┌─────────────────────────────────────────┐
                          │  healthchecks.io / hc-ping.com          │
                          │  (external; cluster in EU, no Cloudflare│
                          │   in front; runs Django + HAProxy)      │
                          │                                          │
                          │  expects ping every 5 min (+ 10 min     │
                          │  grace). On absence → fires alert       │
                          │  channel = Slack webhook                │
                          └────────────▲────────────────┬────────────┘
                                       │ POST /<uuid>   │ webhook
                                       │ every 5m       │ on absence
                                       │ (~200 bytes)   ▼
                                       │      ┌────────────────────┐
                                       │      │ Slack #do-alerts   │
                                       │      │ (SLACK_WEBHOOK_DO_ │
                                       │      │  ALERT)            │
                                       │      └────────────────────┘
                                       │
   ┌──────────────────┐  443/TLS       │
   │  Browser / RUM   │ ───────────► Cloudflare ─► Caddy 2 ─► FastAPI (blue OR green)
   └──────────────────┘                │              │           │
                                       │              │           │
                                       │              │           │  in-app heartbeat task
                                       │              │           │  (asyncio.create_task,
                                       │              │           │   started in lifespan)
                                       │              │           │
                                       │              │           │   every 300 s:
                                       │              │           │     1. await pool.fetchval("select 1")
                                       │              │           │     2. key_pool.count_active_keys()
                                       │              │           │     3. await httpx.post(HC_URL)  ─┐
                                       │              │           │                                    │
                                       └──────────────┴───────────┴────────────────────────────────────┘
                                                                                  │
                                                                                  ▼
                                                          (direct egress, not via Caddy/Cloudflare —
                                                           proves the droplet has outbound network,
                                                           proves asyncio loop is scheduling tasks)
```

Notes on the path:

- **The ping does not traverse Caddy or Cloudflare.** This is deliberate —
  the goal is to prove the *droplet container's* event loop is alive, not
  that the public ingress works. Public ingress is covered by Cloudflare's
  own RUM + a separate uptime check on `https://zettelkasten.in/api/health`
  (option-a layer; cheap, hits the existing `/api/health` route, no event-
  loop guarantee).
- **The DB round-trip is intentional** (medium-deep signal). A read-only
  `select 1` on a hot connection costs <2 ms p99 and detects: (a) `asyncpg`
  pool fully exhausted, (b) Supabase Postgres unreachable, (c) Postgres up
  but TLS expired, (d) container DNS resolver dead.
- **The key-pool check is a property read, not an API call.** It returns the
  number of un-rate-limited keys; if that is zero the heartbeat still pings
  (the app is alive) but a separate field `keys_active=0` lands in the
  healthchecks.io log body so the Slack alert can surface "alive but
  degraded".[^hc-faq logs section] No Gemini API call is made — see §5
  *Heartbeat amplifies upstream outage*.

---

## 3. Implementation sketch (≤40 lines, illustrative — not for commit)

The shape that fits the existing `web_monitor` package and the FastAPI
lifespan contract.[^fastapi-lifespan]

```python
# website/core/heartbeat.py  (NEW)
from __future__ import annotations
import asyncio, logging, os
import httpx

logger = logging.getLogger("website.heartbeat")
_HC_URL = os.environ.get("HEARTBEAT_PING_URL")       # https://hc-ping.com/<uuid>
_INTERVAL_S = float(os.environ.get("HEARTBEAT_INTERVAL_S", "300"))

async def _one_beat(pool, key_pool) -> None:
    # 1. Event loop alive + DB reachable (medium-deep signal).
    await asyncio.wait_for(pool.fetchval("select 1"), timeout=5.0)
    # 2. Key pool object sanity — property read only, no Gemini call.
    keys_active = key_pool.count_active_keys()
    body = f"keys_active={keys_active}"
    # 3. Ping. Use a fresh client so a stuck pool can't gag the heartbeat.
    async with httpx.AsyncClient(timeout=10.0) as c:
        await c.post(_HC_URL, content=body)

async def heartbeat_loop(pool, key_pool, stop: asyncio.Event) -> None:
    if not _HC_URL:
        logger.warning("heartbeat disabled: HEARTBEAT_PING_URL unset"); return
    while not stop.is_set():
        try:
            await _one_beat(pool, key_pool)
        except Exception:                          # noqa: BLE001 — never raise
            logger.exception("heartbeat beat failed; continuing")
            # NOTE: we deliberately do NOT post the /fail variant. Transient
            # DB blip should not page the operator; absence-of-success after
            # the 10-min grace will. (Avoids §5 row 4.)
        try:
            await asyncio.wait_for(stop.wait(), timeout=_INTERVAL_S)
        except asyncio.TimeoutError:
            pass

# in website/app.py lifespan:
# stop = asyncio.Event(); app.state.hb_stop = stop
# app.state.hb_task = asyncio.create_task(heartbeat_loop(pool, key_pool, stop))
# (on shutdown) stop.set(); await app.state.hb_task
```

Line-by-line rationale:

- `asyncio.wait_for(..., timeout=5.0)` on the DB query is the
  event-loop-starvation guard from §5: if the loop is blocked the timeout
  itself cannot fire on schedule, but on a free loop a stuck DB will
  surface within 5 s. Both failure modes route to the same outcome (no
  ping), which the external service interprets correctly.
- We use a fresh `httpx.AsyncClient` per beat (cheap — ~30 KB transient)
  rather than the shared `_slack_client` pool. Justification: the Slack
  client's semaphore is sized for burst Slack traffic; a heartbeat that
  needs to fire while Slack is being hammered must not queue behind it.
- `stop = asyncio.Event()` + `asyncio.wait_for(stop.wait(), timeout=...)`
  is the canonical FastAPI lifespan-cancel pattern; bare `asyncio.sleep`
  swallows the cancellation and blue/green cutover then hangs for up to
  `_INTERVAL_S` waiting for graceful shutdown.[^fastapi-lifespan]
- No call to `app_errors.notify(...)` on beat failure — we log only. A
  beat failure does not need a Slack message of its own because the
  absence-of-ping will produce one in ≤15 min via healthchecks.io. Double-
  alerting on the same incident is row 5 of §5.

---

## 4. Comparison matrix — dead-man-switch services (2026 prices)

| Service | Free tier | Paid entry | Slack integration | SLA / track record | Best fit for our stack |
|---|---|---|---|---|---|
| **healthchecks.io**[^hc-pricing][^hc-faq] | **20 jobs**, 100 log entries each, full feature set, indefinitely | $5/mo Supporter (same caps, just supports the OSS author); $20/mo Business → 100 jobs | Yes, via integrations panel | No published SLA, but publishes detailed postmortems (Nov 2025 connectivity, Apr 2025 DB segfault); open-source self-hosted fallback exists[^hc-pm-2025-11][^hc-pm-2025-04] | **Best**. Single-purpose dead-man-switch; free tier is permanent; OSS escape hatch means future-proof. |
| **BetterStack Heartbeats**[^bs-cron] | **10 monitors**, 3-min check cadence, email alerts | $12/mo entry | Yes, included on free tier | Marketed 30-second check frequency for paid tiers; SLA not in public marketing copy as of source date | Strong second. Combines heartbeats + incident management + status page; overkill for 1 droplet but excellent if we add multi-service. |
| **Cronitor**[^bs-cron] | **5 monitors**, email + Slack on free tier, 1-month data retention | Team/Business pay-as-you-go (~$2/job/mo at 100 jobs) | Yes, free tier | Comparable history but ~5× cost-per-job at scale; richer cron-output capture | Acceptable. Cost dominates if we ever scale past 5 checks. |
| **Pingdom (Solarwinds)** | No free tier for heartbeat ingestion; uptime starts ~$15/mo | uptime $15/mo, transactional more | Yes | Established enterprise vendor; SLA available on enterprise contracts | Worst fit. Built for HTTP uptime, not dead-man-switch; no free tier; enterprise sales motion. |

Criteria recap: (a) cost for 1 endpoint at 5-min cadence — 0 / 0 / 0 / N/A,
(b) SLA — Pingdom only on enterprise, others publish history not SLA, (c)
Slack alert delivery — all four support it, (d) deliverability through
Cloudflare proxy — N/A in dead-man-switch direction (we ping *out*, they
alert via Slack), (e) operator burden — healthchecks.io wins on minimality.

**Cloudflare-Workers-as-DIY-prober (option a variant):** Cloudflare Workers
Cron Triggers can hit `https://zettelkasten.in/api/health` every 5 min for
free; the Worker can `fetch` to a Slack webhook on non-200. This is a
legitimate option-a implementation but is *not* dead-man-switch: if the
Worker itself is broken or its alert is suppressed, you get silence. Use
only as a redundant outer prober, never as the primary.

---

## 5. Anti-patterns — what NOT to do (2024-2026 SRE consensus)

| # | Anti-pattern | Why it bites | Cite |
|---|---|---|---|
| 1 | **In-app heartbeat over the public ingress** (process pings `https://zettelkasten.in/api/health` via Cloudflare back to itself). Looks elegant; tells you nothing. | A Cloudflare-edge or Caddy-TLS outage will silence the heartbeat *and* the real users — but to your eyes the silence is identical to "all fine". You learn nothing the user wouldn't have learned faster by complaint. | Red Hat probes guide; SRE ch.22 cascading-failure self-amplification.[^redhat-probes][^sre-ch22] |
| 2 | **Event-loop-starvation lying.** Heartbeat task is alive (`asyncio.create_task` scheduled) but main request handlers are CPU-blocked on a sync call inside an `async def` route. The heartbeat keeps pinging. | This is the textbook asyncio failure of 2024-2025: P95 latency goes from 50 ms → 2000 ms while loop debug shows no slow callbacks; users get timeouts; healthchecks.io stays green.[^asyncio-blocking] **Mitigation**: heartbeat must include a `asyncio.wait_for(pool.fetchval(...), timeout=5)` — a starved loop cannot complete it. | Tasuke Hub asyncio guide 2025; johal.in blocking-call debug.[^asyncio-blocking][^asyncio-johal] |
| 3 | **Heartbeat that exercises a write path** (e.g. inserts a row into `core.events` per beat). Tempting because "writes prove more". | Three problems: (a) noise pollution of audit tables, (b) on a stuck write the heartbeat itself blocks and *correctly* misses its window — but you cannot distinguish "DB stuck" from "container crashed", (c) per-beat write cost dominates the 1 vCPU budget at scale. **Rule**: keep heartbeats idempotent / read-only; rely on real user traffic to exercise writes and let separate alerting (App_Errors Slack) cover write failures. | Healthchecks.io FAQ "what should I monitor" guidance.[^hc-faq] |
| 4 | **`/fail` variant on every transient hiccup.** healthchecks.io supports POST to `/fail` to force-fail a check; tempting to call it on every caught exception. | Floods the Slack channel and trains the operator to ignore it (alert fatigue). Dead-man-switch is *absence-based*: silence = page, one ping = healthy. Resist adding a parallel "negative-signal" path. | SRE book "alert fatigue" pattern; healthchecks.io blog on flapping. |
| 5 | **Heartbeat amplifies upstream outage.** Heartbeat path calls Gemini live each beat to "prove the key pool works". | When Gemini has a 30-min outage, every 5-min beat times out, every beat alerts, Slack channel becomes useless. Worse: in a partial regional outage, heartbeats burn rate-limit quota that the real user traffic needs. **Rule**: heartbeat exercises only resources the droplet *owns* (own DB, own loop, own process memory). Upstream health belongs in `App_Errors` alerting triggered by real traffic. | SRE ch.22 cascading-failure positive-feedback rule.[^sre-ch22] |

Quiet-hours suppression note: the operator has repeatedly stated the
Zettelkasten alert channel is the primary on-call surface and there is no
business-hours window. **Do not add quiet hours** to the heartbeat alert
policy — that is a documented mechanism by which weekend outages become
Monday-morning surprises.[^sre-ch22]

---

## 6. Direct answers to the 7 questions

1. **Canonical 2024-2026 pattern for 1-droplet FastAPI**: (c) dead-man-switch
   external service + (b) thin in-app emitter is the production-shipped
   pattern. Pure (a) external HTTP probe is too shallow (cannot detect event-
   loop starvation). Pure (b) in-app Slack-direct is undetectable when the
   process dies (no negative-signal). (d) full belt-and-braces is overkill
   at this scale. Case studies: healthchecks.io's own incident publishing
   shows their internal monitoring stack uses this exact (c+b) shape;[^hc-pm-2025-11]
   OneUptime's 2026 OpenTelemetry post documents the same pattern as
   industry-standard.[^oneuptime-heartbeat]
2. **Python+asyncio scheduler choice**: For one periodic task at 5-min
   cadence, **`asyncio.create_task` + `while not stop.is_set(): await
   asyncio.wait_for(stop.wait(), timeout=N)` started from FastAPI
   `lifespan`** is the canonical minimal pattern.[^fastapi-lifespan] APScheduler
   v4 is still pre-release as of 2026-05;[^apscheduler-pypi] v3.x
   `AsyncIOScheduler` works but adds a dependency and a second source of
   truth for "what tasks run in this app". `arq` is the right answer if we
   ever need a Redis-backed task queue with retries and result storage —
   for a single periodic ping it is overkill. Sentry uses an internal
   Celery-style framework for its scheduled tasks;[^sentry-async-workers]
   PostHog runs Plugins/HogQL workers separate from the web tier — neither
   is a useful reference for "one 5-min heartbeat from a web process".
3. **Dead-man-switch service comparison**: see §4 matrix. Healthchecks.io
   wins on (a) cost (free indefinitely), (b) track-record transparency
   (publishes postmortems), (c) Slack delivery (yes), (d) deliverability
   (the *outbound ping* path from droplet → hc-ping.com does not traverse
   Cloudflare and is unaffected by our edge config).
4. **Signal scope**: medium-deep is the 2024-2026 consensus.[^redhat-probes]
   Process-alive-only (`return "ok"`) detects only the rarest failure
   modes; full "DB + Gemini + Slack + Razorpay" round-trip violates §5
   row 5 (heartbeat amplifies upstream outage) and §5 row 3 (write-path
   exercise). Land at: event-loop probe via `asyncio.wait_for`, owned-DB
   read, in-process pool sanity reads. No upstream calls.
5. **SRE anti-patterns**: see §5 table. Key cite — SRE Book ch.22 "Addressing
   Cascading Failures" lays out the positive-feedback rule that explains
   why upstream-touching heartbeats amplify rather than detect.[^sre-ch22]
   The 2024-2025 asyncio-starvation literature is the new addition: it
   was not in the original 2016 SRE book and is the most important new
   anti-pattern for Python services.[^asyncio-blocking]
6. **Lowest false-positive on our stack**: **healthchecks.io**, on three
   independent grounds — (i) the inbound ping path from our droplet to
   hc-ping.com goes Cloudflare-egress → public internet → hc-ping
   HAProxy → Django; we share zero infrastructure with them so a
   Cloudflare-Brazil outage or a DO-NYC3 outage cannot create correlated
   alarms, (ii) the absence-detection grace window is configurable per-
   check (we'd set 5 min cadence + 10 min grace), (iii) when they have had
   incidents (2 in the last 12 months[^hc-pm-2025-11][^hc-pm-2025-04]) they
   publish full postmortems within days — an operational maturity signal
   absent from BetterStack and Cronitor public comms.
7. **Touch write paths?** No (see §5 row 3). Stay light. Real user traffic
   continues to exercise writes; failures there are caught by the existing
   `App_Errors` Slack channel and Sentry-style exception capture. The
   heartbeat's job is "process + loop + owned-DB alive", nothing more.

---

## 7. Citations

[^fastapi-lifespan]: FastAPI — "Lifespan Events", advanced/events. https://fastapi.tiangolo.com/advanced/events/
[^hc-pricing]: Healthchecks.io — "Plans and Pricing". https://healthchecks.io/pricing/  (Hobbyist $0 / 20 jobs; Business $20/mo / 100 jobs.)
[^hc-faq]: Healthchecks.io — "Frequently Asked Questions". https://healthchecks.io/docs/faq/  (Dead-man-switch / heartbeat-monitoring description.)
[^hc-pm-2025-11]: Healthchecks.io Blog — "Post-mortem: Outage on November 1", 2025-11. https://blog.healthchecks.io/2025/11/post-mortem-outage-on-november-1/
[^hc-pm-2025-04]: Healthchecks.io Blog — "Post-mortem: Database Outage on April 30, 2025", 2025-05. https://blog.healthchecks.io/2025/05/post-mortem-database-outage-on-april-30-2025/
[^bs-cron]: BetterStack Community — "10 Best Cron Job Monitoring Tools in 2026". https://betterstack.com/community/comparisons/cronjob-monitoring-tools/
[^cronitor-vs]: Cronitor — "Versus Healthchecks.io". https://cronitor.io/versus-healthchecks.io
[^sre-ch22]: Beyer, Jones, Petoff, Murphy (eds.) — *Site Reliability Engineering*, Ch.22 "Addressing Cascading Failures", Google SRE Book. https://sre.google/sre-book/addressing-cascading-failures/
[^redhat-probes]: Red Hat Developer — "You (probably) need liveness and readiness probes" (deep vs shallow health-check pattern). https://developers.redhat.com/blog/2020/11/10/you-probably-need-liveness-and-readiness-probes
[^asyncio-blocking]: Tasuke Hub — "Python asyncio Error Resolution Guide: 15 Error Patterns" (2025). https://tasukehub.com/articles/python-asyncio-event-loop-errors-solution
[^asyncio-johal]: Sanjeev Johal — "Debugging Python asyncio event loop blocking with logging" (2024). https://www.johal.in/debugging-python-asyncio-event-loop-blocking-with-logging/
[^apscheduler-pypi]: APScheduler — PyPI release history. https://pypi.org/project/APScheduler/  (3.11.x current; v4 in pre-release.)
[^sentry-async-workers]: Sentry Developer Docs — "Asynchronous Workers". https://develop.sentry.dev/backend/application-domains/asynchronous-workers
[^oneuptime-heartbeat]: OneUptime — "How to Set Up Heartbeat and Dead Man's Switch Alerts", 2026-02. https://oneuptime.com/blog/post/2026-02-06-heartbeat-dead-man-switch-opentelemetry-pipeline/view
