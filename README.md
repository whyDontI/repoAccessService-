# Repository Access Service

## Running instructions
No frontend yet, so `docker compose up` brings up Postgres + the backend API only.

```bash
docker compose up -d --build
# fixture data seeds automatically on backend startup (idempotent -- safe to restart)
curl "http://localhost:8000/check?user_id=1&action=read&repo_id=100"
```

Running the backend outside Docker (e.g. for local iteration) still works the same as
before:
```bash
docker compose up -d postgres
cd backend && pip install -r requirements.txt
DATABASE_URL="postgresql://repo_access:repo_access@localhost:5432/repo_access" \
  python scripts/generate_fixture.py
DATABASE_URL="postgresql://repo_access:repo_access@localhost:5432/repo_access" \
  uvicorn app.main:app --reload
```

Tests need a SEPARATE database (they `TRUNCATE` it on every run, so it must never be
the dev/fixture one):
```bash
createdb -h localhost -p 5432 -U repo_access repo_access_test   # PGPASSWORD=repo_access
psql -h localhost -p 5432 -U repo_access -d repo_access_test -f backend/app/schema.sql
cd backend && python3 -m unittest tests.test_cache tests.test_checker tests.test_oracle tests.test_routes tests.test_db -v
```

## Current state
Built: DB schema, fixture generator (with planted cycles/edge cases, now idempotent),
the in-memory cache, `checker.check()` / `checker.explain()` (R1 + R3), the full API
(`/check`, `/explain`, membership + grant mutations, browse endpoints), the oracle, the
R4 comparison harness, the load generator, and `docker-compose.yml` + `backend/Dockerfile`
-- all with passing tests (42 total), plus live end-to-end passes over real HTTP,
against the real fixture, and a clean-slate `docker compose up` verification. See "R4
-- divergences found" and "Load testing -- what we found so far" below for the real
bugs and open questions those surfaced.

Not built yet: the frontend and its Dockerfile/compose service, and `BENCH.md`'s formal
resource-constrained benchmark run.

## Requirements and tradeoffs

### Fast checks vs fast revocation
- Cache: per user, the full set of teams they belong to (direct + nested).
- Never cache grants — always read them live.
- Check = grants for `user + team set`, on repo or its org, take highest role.
- Live grants means revocation is instant, no cache expiry to wait on.

**Invalidation:**
- User added/removed from a team → clear just that user's cache entry.
- Team re-parented → clear the whole cache (no reverse map to know who's affected).

**The bet:** team re-parenting is rare. If it's not, cache runs cold a lot and latency
suffers. We're saying that upfront rather than hiding it.

**Considered and rejected:** caching the yes/no answer directly. Throws away the "why"
explain needs, and one team change would mean recomputing every resource every
affected user can reach — bigger blast radius than what we picked.

**What would change our mind:** evidence that teams get re-parented often. Then we'd
want a reverse map (team → users under it) so invalidation can be targeted instead of
wiping everything.

### Explain (R3)
- Doesn't touch the cache — cache has no "how you got there" info.
- Walks the graph fresh, builds the readable chain each time.
- Fine perf-wise: not in the 5k/sec check path.
- Denies get explained too: "no path" vs "path found, role too low."

### Team hierarchy isn't a strict tree
- Nothing stops a user or team from having more than one parent.
- Treated as a graph. Always track visited teams to avoid infinite loops.

### Org grants cover their repos, but don't cap them
- An org grant reaches all repos under it — but a repo-level grant can still be higher.
- Example: read on the org, write directly on one repo → that repo checks as write.
- Checked live — climb from repo to its org, compare grants at both, take the higher.
- Not precomputed.

### We generate the test data, cycles included
- The assignment expects cycles and oracle-vs-fast-path disagreements to show up.
- Since we write the generator, we plant a few cycles and edge cases on purpose
  (empty team, boundary-role grant, org-only access) — otherwise there's nothing for
  the comparison step to actually find.

### The mutation script moves people between teams too
- Not just grants. The clearest hard revocation case (per the assignment) is removing
  someone from a big team — a membership change.
- Adds happen a lot, removes less, team re-parenting stays rare on purpose
  (roughly 100 adds : 5 removes).

### No compose file with limits was given to us
- We wrote our own, capped at 2 vCPU / 4GB.

### No Redis, no second cache
- One backend process, one in-memory cache.
- Syncing two caches across machines is a real problem, just not one we have here.

### Only handling depth up to the fixture's 8 levels
- Not engineering for deeper/wider than our own test data right now.

### GET /grant always requires a subject_id
- The spec just says "list / create grants" -- underspecified on what "list" scopes to.
- The grants table is huge (~400k rows in the fixture alone), so an unfiltered dump
  isn't a real option.
- Resolved it as the mirror image of `/resource/{id}/grants`: that endpoint answers
  "what grants exist on this resource," `GET /grant?subject_id=` answers "what grants
  does this subject have" -- both directions covered, neither needs pagination for a
  reasonable scope.

### All SQL is parameterized, no exceptions
- Every query in the backend passes user-controlled values (`user_id`, `repo_id`,
  `subject_id`, role strings, etc.) through `asyncpg`'s placeholders (`$1`, `$2`, ...),
  never string-built into the query itself. The oracle follows the same rule.
- The only f-strings touching a query anywhere in the backend build display text or
  error messages (e.g. `"No resource with id {resource_id}"`), never SQL.
- No user input is ever eligible for SQL injection here, by construction, not by
  review -- there's simply no code path where a request value gets concatenated into
  a query string.

## R4 -- divergences found

Ran `oracle_harness.py` against the full ~400k-grant fixture:
- 1,000 rounds at a 0.3 mutation rate, then 5,000 rounds at 0.4 -- 0 divergences.
- 14,000 checks targeted specifically at the 70 users sitting directly under the
  fixture's 3 deliberately-cyclic teams (the case the assignment calls out by name) --
  0 divergences.

A "0" is worthless if the comparison itself doesn't actually work, so before trusting
it: temporarily disabled org-grant climbing in a copy of `resolve_role` (a real,
plausible bug -- "forgot to check the org too") and re-ran against 300 real
org-grant-covers-a-repo cases. Result: 300/300 mismatches, 100% detection. The harness
does catch real bugs; it isn't just agreeing with whatever `checker.py` says.

**Why zero is a defensible result here, not a sign the harness is broken:**
- `checker.check()` does use a cache, but only for the team-set lookup (which teams a
  user transitively belongs to) -- never for grants. `resolve_role()` reads the
  `grants` table live on every call, cache hit or miss on the team set. The oracle
  never caches anything at all. So even on a cache hit, the actual access decision is
  always made from fresh grant data in both implementations -- there's no path where
  they could be looking at different grant data at the same moment. (A stale *team
  set* is possible in principle if invalidation were ever missed, but that would be a
  bug in the invalidation wiring, not a gap in this design -- and it's exactly what
  the harness's membership-mutation testing above is checking for.)
- Both handle cycles with a visited-set, just differently (the fast path per-path,
  inside a recursive CTE; the oracle a single global set in a plain BFS). Reachability
  is a well-defined set regardless of which strategy computes it, so two *correct*
  cycle guards can't disagree on the final answer, only on how much work they do to
  get there.
- Org-vs-repo precedence (climb from repo to org, take the max) is implemented the
  same logical way in both, written independently.

**What would make us look again:** any future change to `checker.py` or `oracle.py`
that isn't re-run through this harness before being trusted. This isn't a one-time
proof -- it's meant to be re-run whenever the fast path changes.

## Load testing -- what we found so far

`load_test.py` paces `/check` at a target rate while separately applying membership
mutations, then reports p50/p95/p99. Building and validating it against the real
server surfaced three real bugs before it ever produced a trustworthy number:

- **No error handling on mutations.** `_apply_mutation` had none -- one failed request
  crashed the entire run. `_timed_check` already caught this correctly; the mutation
  path didn't match it. Fixed.
- **A real race condition in `db.py`'s lazy pool initialization.** `if _pool is None:
  _pool = await asyncpg.create_pool(...)` has no lock. Under a cold-start burst of
  concurrent requests, every one of them sees `_pool is None` at the same instant and
  independently calls `create_pool()` -- instantly opening far more connections than
  intended, all at once, blowing straight through Postgres's own `max_connections`
  (100 by default) with `TooManyConnectionsError: sorry, too many clients already`.
  This looked at first like a resource-sizing problem; the traceback showed it was
  actually a concurrency bug in pool creation itself. Fixed with an `asyncio.Lock`
  (check-lock-check, so only the first caller actually creates the pool).
- **`asyncpg`'s own default pool size (`max_size=10`) is too small for this load.**
  Now configurable via `DB_POOL_MAX_SIZE` (default 20 -- deliberately not higher:
  Postgres's `max_connections` default is 100 on a container capped at 512MB, and
  other clients (the harness, this script's own setup connection) need headroom too).

**What's still unresolved, on purpose:** even after all three fixes, sustained
paced runs show real, significant run-to-run variance -- e.g. one clean-environment
100/sec run measured *worse* (p95 204ms) than a 200/sec run right after it (p95 6ms).
That's arrival-rate-vs-service-rate queueing behavior, not noise, but pinning down the
actual sustainable ceiling under the real 2 vCPU / 4GB constraint needs the kind of
repeated, controlled measurement `BENCH.md` is for -- not a quick validation pass on a
single developer machine outside Docker. Flagging it here rather than either hiding it
or trying to resolve it on the spot.

## Decisions

- **`generate_fixture.py` had to become idempotent.** Discovered during oracle-harness
  testing that re-running it against a non-empty database throws a duplicate-key
  error. Since `docker compose up` needs to seed data on every backend startup,
  including against a volume that already has data from a previous run, it now checks
  `subjects` first and skips generation entirely if any rows exist.

- **Cache stores a timestamp too, not just the team set.** Plan called for
  `get(user_id) -> set[int] | None`. Changed it to also return when that set was
  cached, because the frontend needs an "as of" time on cache-hit answers, and the
  cache is the only place that timestamp exists.

- **`explain()`'s chain is a plain list of strings, not a structured step type.**
  Originally planned a richer `ExplainStep` (subject name + type) separate from the
  resource/role part. Simplified to one flat list of readable strings (e.g.
  `["alice", "platform-eng", "acme-corp (write)", "repo: api-gateway"]`), matching the
  assignment's own example format directly — a breadcrumb the frontend can just join
  and display, instead of a type it has to format itself.

- **A real bug caught by testing, not just a process hiccup this time:** the first
  version of `test_routes.py` pre-created the `asyncpg` pool in its own `setUp` via
  `asyncio.run(...)`, then handed it to a FastAPI app being driven by `TestClient`.
  `TestClient` runs requests in its own separate event loop, and `asyncpg` pools are
  bound forever to the loop that created them — every request failed with `RuntimeError:
  ... attached to a different loop`. Fixed by never pre-creating the pool: point
  `db.DATABASE_URL` at the test database and let the app create its pool lazily, the
  first time a request actually needs it, inside `TestClient`'s own loop. Closing it
  afterward had the same problem in reverse (closing from a *different* new loop than
  the one that made it) — fixed by giving the FastAPI app a real `lifespan` shutdown
  hook that closes its own pool, since `TestClient.__exit__` runs that shutdown inside
  the correct loop. This isn't just a test workaround: the app didn't have a clean
  shutdown path for its DB pool before this, so it's a real fix either way.

- **A dead end worth recording, since transcripts should show these, not hide them:**
  while spot-checking `explain()` against the real fixture, results came back wrong
  (a confirmed-working grant showed as "no path found"). Before assuming `checker.py`
  was broken, checked the data directly — the `repo_access` database was empty. Traced
  it to an earlier `docker compose down -v` (destructive, removes volumes) run during
  cleanup after an earlier step. Regenerated the fixture and it was correct. Also used
  the opportunity to directly verify the test suite isn't the culprit: ran the fixture
  generator, confirmed data present, ran the full test suite, confirmed data was
  *still* present immediately after — proving `test_checker.py`'s per-test `TRUNCATE`
  only ever touches the separate `repo_access_test` database, never the dev one.
