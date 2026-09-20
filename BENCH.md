# BENCH.md

Real numbers, measured on this machine (Apple Silicon Mac, Docker Desktop), against
the actual resource-limited containers defined in `docker-compose.yml` (backend: 1.0
vCPU / 1536MB, postgres: 0.5 vCPU / 512MB) and the real fixture (50k users, 4k teams,
20k repos, ~400k grants).

## How this was measured

- Load generator: `backend/scripts/load_test.py`, run via `docker compose run --rm
  backend ... python scripts/load_test.py --url http://backend:8000` -- i.e. **from
  inside the compose network, container to container**, not from the host.
- This distinction matters and is itself a finding -- see below.
- Dataset: the real fixture, unmodified counts (50,000 users / 4,000 teams / 20,000
  repos / ~400,002 grants).
- `load_test.py` reserves 10% of checks for the fixture's known deepest-nesting users
  (`fixture_manifest.json`'s `deep_user_ids`, an 8-level chain), so every run already
  includes the deepest-nesting case, not just easy ones.
- Both cold (right after a fresh `docker compose up`/restart) and warm (after some
  traffic has landed) numbers are reported below, explicitly labeled -- conflating the
  two would misrepresent what's actually being measured.

## Finding #1: measuring through the host-published port is not valid on this machine

The very first thing this benchmark run turned up wasn't about the app at all.
Hitting `http://localhost:8000` from the host (the natural way to test a compose
stack) gave wildly bad, inconsistent numbers -- e.g. 1,000 checks/sec measuring p50 in
the *seconds*. CPU on both containers stayed under 50% of their limits throughout,
which doesn't fit "the app is overloaded." Re-running the identical load generator
from *inside* the compose network (`docker compose run`, target `http://backend:8000`)
against the same containers dropped p50 from ~4,400ms to ~4ms for the same 1,000/sec
load. Docker Desktop's host↔container networking on macOS is virtualized and adds
latency that has nothing to do with the service being tested. All numbers below are
measured container-to-container, which is the only way this benchmark reflects the
actual application rather than the laptop's networking stack.

## Finding #2: cold start pays a real, one-time cost

Immediately after a fresh backend restart, the first batch of traffic is slow --
not because of a code path issue, but because Postgres's buffer cache and the OS page
cache for the (small) dataset are cold, so the first pass through
`team_relationships`/`grants` pays real disk I/O. A single deep-nesting check
(8-level chain, cache miss) measured **16ms cold**, just over the 10ms budget, then
**~0.9ms on every subsequent call** to the same user. This is expected and consistent
with the design: R2's cache is about revocation correctness, not about avoiding this
one-time infrastructure warm-up, which is a property of Postgres/the OS, not of
`checker.py`.

## Finding #3: real sustained throughput ceiling is well below 5,000/sec

With a warm backend, sustained load was tested at increasing rates:

| Check rate | p50 | p95 | p99 | Failures |
|---|---|---|---|---|
| 500/sec | 1.3ms | 3.6ms | 59ms | 0 |
| 1,000/sec | 1.3ms | 2.1ms | 11ms | 0 |
| 1,000/sec + 10 mutations/sec | 2.4ms | 53ms | 84ms | 0 (15,000 attempted) |
| 1,500/sec | 1.7ms | 3,166ms | 4,841ms | 0 |
| 2,000/sec | 15,625ms | 22,303ms | 22,365ms | 13,470 / 15,955 |
| 2,500/sec | 16,234ms | 17,171ms | 17,498ms | 13,939 / 14,918 |

**1,000/sec is comfortably inside budget. Past ~1,500/sec, the system doesn't degrade
gracefully -- it collapses.** p50 stays fine even at 1,500/sec while p95/p99 already
show the queue building; by 2,000/sec nearly everything times out.

**The bottleneck, specifically:** it is not the database. Individual queries (the
recursive CTE for a cache miss, the live grants lookup) measured sub-millisecond to a
few milliseconds throughout, including during collapse -- confirmed by the fact p50
stays low even at 1,500/sec while p95 does not, which is the signature of a queue
backing up in front of otherwise-fast work, not of the work itself getting slower.
Postgres CPU never exceeded ~50% of its 0.5 vCPU limit at any tested rate. Backend CPU
never exceeded ~55% of its 1.0 vCPU limit either. **Neither container was
resource-exhausted when the system collapsed.** The ceiling is the single `uvicorn`
worker process's capacity to accept and multiplex that many concurrent connections at
all -- a single Python asyncio event loop handling socket I/O, HTTP parsing, and
request dispatch for thousands of concurrent in-flight requests, not the check logic
itself.

**Also tested and ruled out as the primary cause:** `DB_POOL_MAX_SIZE`. Raising it
from 20 to 50 made p50 *worse* (3,545ms vs 4ms) at the same rate; dropping it to 10
was worse than 20 but better than 50. The relationship isn't monotonic across repeated
trials, which points at noise in a shared connection pool competing for a CPU-limited
Postgres rather than a simple "more connections = more throughput" story. 20 (the
already-committed value, chosen with the same reasoning before this benchmark) is kept
as a reasonable middle point; this is not a settled, thoroughly-tuned optimum.

## What this means for the R1/R2 tradeoff

This is the tension the assignment names directly: "at 2 vCPU there is no
configuration where both are comfortable." The measured evidence says the same thing
concretely. Getting real capacity past ~1,500/sec would mean running more than one
backend worker process -- but this design's whole cache strategy (see README) is a
plain Python dict in one process's memory. Multiple worker processes would each have
their *own* cache, silently breaking the invalidation guarantees R2 depends on (a
mutation would only invalidate one worker's cache, not the others'), unless a shared
cache (Redis, an out-of-scope external service) or a smarter partitioning strategy
were introduced.

**What was chosen:** stay single-process, accept a lower real ceiling than the
assignment's 5,000/sec headline number, and be honest about it here rather than
report a number achieved under conditions (like measuring through the host port) that
don't reflect the real system.

**What would change this:** evidence that check traffic in practice needs to sustain
well above ~1,500/sec on this exact 2 vCPU budget would be the trigger to revisit
single-process-with-local-cache as the design, and look at either a shared external
cache or a sharding strategy that keeps a given user's checks pinned to one worker.

## Reproducing this

```bash
docker compose up -d --build
docker compose run --rm --entrypoint sh backend -c \
  "pip install -q httpx2 && PYTHONPATH=. python scripts/load_test.py --check-rate 1000 --mutation-rate 10 --duration 15 --url http://backend:8000"
```
Restart the backend (`docker compose restart backend`) first if you want the cold-start
numbers instead of warm ones.
