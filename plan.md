# Plan

Working notes. This file can be as detailed as it needs to be — the "keep it short"
rule in CLAUDE.md only applies to README.md and other docs, not this file. Update
checkboxes and details as we build.

## Grading, in short
- Tradeoffs + ambiguities = half the grade.
- Code quality = another quarter.
- Change request = 15%.
- Benchmarks = only 10%.
- Finishing every feature = not graded at all.
- So: write decisions down as we go, don't rush frontend polish.

## Must not skip
- `docker compose up` works on a clean machine, no manual steps.
- Oracle (R4) is required, and can't share code with the fast path.
- Commit as we go, real messages.
- Save raw agent logs to `agent-transcripts/`.

## The core design
- Cache: per user, the full set of teams they belong to (direct + nested).
- Never cache grants. Always read them live.
- Check = look up grants for `user + their team set`, on the repo or its org, take the highest role.
- Org grants cover their repos, but a repo-level grant can be higher — we always take the max of both, checked live, never precomputed.
- Live grants → revocation is instant. No cache to expire.

**Cache invalidation:**
- User added/removed from a team → clear just that user's cache entry.
- Team moved under a new parent → clear the whole cache (can't tell who's affected without a reverse map).
- Grant added/removed → nothing to invalidate, grants were never cached in the first place.

**The bet:** team re-parenting is rare. If it's not, cache goes cold often and latency suffers. We say this plainly in README, not hide it.

**Rejected alternative:** caching the yes/no answer itself. Kills the "why" explain needs, and one team change would mean recomputing every resource every affected user could reach.

## Explain (R3)
- Doesn't use the cache — cache has no "how you got there" info.
- Walks the graph fresh each time, builds the readable chain.
- Fine perf-wise: explain isn't in the 5k/sec path, only check is.
- Denies need explaining too: "no path found" vs "path found, role too low."

## Data decisions
- Team membership is a graph, not a strict tree — nothing stops multiple parents.
- Always track visited teams to avoid infinite loops.
- We write the fixture generator, so cycles in the data are ours to add on purpose (needed for R4 to find anything).
- Also seed a few edge cases: empty team, boundary-role grant, org-only access, a repo grant that beats its org grant.
- Mutation script should move people between teams too, not just touch grants — that's the real revocation case R2 is about.
- Adds >> removes >> re-parenting, in that order of frequency (e.g. ~100 adds : 5 removes, re-parenting rare).
- No compose file with limits was given to us — we write our own, capped at 2 vCPU / 4GB.
- No Redis, no second cache. One process, one dict. Syncing two caches is a problem we don't have.
- Only handling nesting up to the fixture's 8 levels for now.
- Cycle check = just track visited team ids, nothing fancier.

## Technical choices (decided up front)
- Async DB access throughout (not sync + threadpool) — keeps the in-memory cache single-threaded, no locks needed.
- Raw SQL, no ORM. Small schema, and the recursive CTE for ancestor lookup is easier to reason about as plain SQL than through an ORM abstraction we'd use once.
- Compute a user's team set in **one recursive SQL query**, not one DB round-trip per level.
- Postgres won't stop a recursive query on a cycle by itself — carry a visited list in the query, cut off repeats.

## Watch out for
- A user in lots of overlapping teams might make the grants lookup bigger than expected — sanity check once fixture exists.
- If the load test ends up moving teams around more than intended, latency will look worse — report it honestly, don't tune it away.
- Grant mutations were originally assumed to be the thing needing invalidation — they don't. Only membership mutations do. Make sure the API and mutation scripts reflect that, not the older grant-centric assumption.

---

## File-by-file plan

### Top level
```
repoAccessService/
├── docker-compose.yml
├── backend/
├── frontend/
├── BENCH.md
├── README.md
├── plan.md
├── CLAUDE.md
└── agent-transcripts/
```

### `docker-compose.yml`
- Services: `postgres`, `backend`, `frontend`.
- `postgres`: official image, volume for data, runs `schema.sql` on init.
- `backend`: builds `backend/Dockerfile`, depends on postgres, reads DB connection from env.
- `frontend`: builds `frontend/Dockerfile`, depends on backend.
- `deploy.resources.limits` split across services so the total stays at 2 vCPU / 4GB.

### `backend/Dockerfile`
- Python base image, install `requirements.txt`, copy `app/`, run with uvicorn.

### `backend/requirements.txt`
- fastapi, uvicorn, asyncpg (async Postgres driver — no ORM), pydantic.

### `backend/app/schema.sql`
- Table `subjects`: id, type (user/team), name.
- Table `resources`: id, name, type (org/repo), org_id (nullable, set for repos).
- Table `team_relationships`: id, subject_id, belongs_to_team_id (nullable for a top-level team).
- Table `grants`: id, subject_id, role, resource_id.
- Indexes: `team_relationships(subject_id)`, `team_relationships(belongs_to_team_id)`, `grants(subject_id, resource_id)`, `grants(resource_id)`.

### `backend/app/db.py`
- `get_pool()` — creates/returns the shared asyncpg connection pool, read from env var for the DB URL. One pool for the whole app, created on startup.

### `backend/app/models.py`
- `Role` enum (IntEnum): `READ < WRITE < ADMIN < OWNER`, so comparisons (`role >= action`) just work.
- `CheckResponse` — `{allowed: bool, as_of: datetime}`.
- `ExplainStep` — one hop in the chain (subject name, subject type).
- `ExplainResponse` — `{allowed: bool, chain: list[ExplainStep], granted_role: Role | None, reason: str}`.
- `GrantIn` / `GrantOut` — subject_id, role, resource_id.
- `MembershipIn` — subject_id, belongs_to_team_id.
- `OrgOut`, `RepoOut`, `TeamOut` — basic browse shapes.

### `backend/app/cache.py`
- Module-level dict: `_cache: dict[int, tuple[set[int], datetime]]`.
- `get(user_id) -> set[int] | None` — returns the cached team set, or `None` on a miss.
- `set(user_id, team_ids: set[int])` — stores the set with a timestamp.
- `invalidate_user(user_id)` — deletes that one entry, no-ops if absent.
- `invalidate_all()` — clears the dict.
- No locking needed — async, single event loop, no I/O happens mid-mutation.

### `backend/app/checker.py` — the fast path (R1 + R2 + R3)
- `get_team_set(user_id) -> set[int]` — checks `cache.get`; on a miss, runs the recursive CTE against `team_relationships` (with a visited-array cycle guard), stores the result via `cache.set`, returns it.
- `resolve_role(subject_ids: set[int], repo_id: int) -> Role | None` — one live query against `grants`, matching `subject_id` in `subject_ids` and `resource_id` in `{repo_id, repo's org_id}`. Returns the highest role found, or `None`.
- `check(user_id, action: Role, repo_id: int) -> bool` — `team_set = get_team_set(user_id)`; `role = resolve_role({user_id} | team_set, repo_id)`; return `role is not None and role >= action`.
- `explain(user_id, action: Role, repo_id: int) -> ExplainResponse` — never touches the cache. Walks `team_relationships` from `user_id` outward with its own visited-set, tracking the parent of each team visited. At every subject visited (the user, then each team), checks `grants` against the repo and its org. Picks the highest role found anywhere in the walk and reconstructs the specific chain that produced it (or, on a deny, the closest chain and the role gap, or "no path found" if nothing connects at all).

### `backend/app/oracle.py` — R4, standalone
- Does **not** import anything from `checker.py` or `cache.py`.
- `oracle_check(user_id, action: Role, repo_id: int) -> bool` — plain recursive Python walk over `team_relationships` (one query per level is fine here, it's not perf-critical), visited-set to guard cycles, collects every reachable subject id, queries `grants` directly against the repo and its org, takes the max role, compares to `action`.
- Deliberately simple — short enough to read start to finish and believe it's correct.

### `backend/app/routes.py`
- `GET /check` — `user_id`, `action`, `repo_id` query params → `CheckResponse` via `checker.check`.
- `GET /explain` — same params → `ExplainResponse` via `checker.explain`.
- `GET /orgs` — list resources where type = org.
- `GET /orgs/{id}/repos` — list repos under an org.
- `GET /resource/{id}` — single resource detail.
- `GET /resource/{id}/grants` — grants attached to a resource.
- `GET /teams/{id}` — a team's parent + direct children.
- `GET /grant`, `POST /grant` — list / create grants. **No cache invalidation on this path** — grants are never cached.
- `POST /membership`, `DELETE /membership/{id}` — add/remove a subject from a team. Calls `cache.invalidate_user` if the subject is a user, `cache.invalidate_all` if the subject is a team (re-parenting).

### `backend/scripts/generate_fixture.py`
- `generate_users(n)`, `generate_teams(n, max_depth)`, `generate_repos(n, orgs)`, `generate_grants(n, subjects, resources)`.
- Seeded RNG for reproducibility.
- Deliberately plants: a handful of cyclic team edges, an empty team, a boundary-role grant, an org-only-access repo, a repo grant that overrides its org grant.
- Bulk-inserts into Postgres (batched `INSERT`, not row-by-row) to keep generation fast at this scale.

### `backend/scripts/oracle_harness.py` — R4 harness
- `random_check()` — picks a random `(user_id, action, repo_id)`.
- `random_mutation()` — applies a random grant or membership change, weighted like the load generator's mix.
- `run_harness(num_rounds)` — interleaves checks and mutations, running each check through both `checker.check` and `oracle.oracle_check`, logging any disagreement with enough detail (ids, timing) to investigate afterward.
- Output feeds the "divergences we found" writeup — this script finds them, we explain them by hand.

### `backend/scripts/load_test.py`
- CLI flags: `--check-rate`, `--mutation-rate`, `--duration`, `--url`.
- `run_checks(rate, duration)` — fires `/check` at the target rate, records latency per request.
- `run_mutations(rate, duration)` — fires membership/grant mutations at the target rate, using the same adds-heavy mix as the fixture edge cases.
- Runs both concurrently, reports p50/p95/p99 at the end.

### `backend/tests/`
- `test_cache.py` — get/set/invalidate_user/invalidate_all, plain dict behavior, no mocking.
- `test_checker.py` — real Postgres (test schema), covering: direct grant, team-inherited grant, org-level grant, repo grant overriding org grant, deny (no path), deny (role too low), a cyclic team doesn't hang.
- `test_oracle.py` — same scenarios against `oracle_check`, independently.

### `frontend/Dockerfile`
- Node base image, install deps, build, serve.

### `frontend/src/api.ts`
- Typed fetch wrapper around the backend endpoints. Types kept in sync with the backend's Pydantic models (this is the typed contract requirement) — either hand-written to match or generated from the OpenAPI schema FastAPI already exposes.

### `frontend/src/App.tsx`
- Top-level layout: org/repo browser on one side, check panel on the other.

### `frontend/src/components/CheckPanel.tsx`
- User search input, repo/org dropdown, action dropdown, check button.
- Shows the result plus an "as of" timestamp, and is upfront that a cache-hit answer reflects the cached team set as of that time (grants themselves are always live).

### `frontend/src/components/ExplainView.tsx`
- Renders the chain from `/explain` as a readable breadcrumb, for both allows and denies.

### `frontend/src/components/OrgBrowser.tsx`
- Browse orgs → repos, view a resource's grants, view a team's nesting.

---

## Build order
Cut from the bottom if short on time.

- [x] `backend/app/schema.sql` + apply to postgres (also stood up `docker-compose.yml` with the postgres service ahead of schedule, so we could test in a real container instead of a throwaway local instance)
- [x] `backend/scripts/generate_fixture.py` (50k users, 4k teams/8 levels, 20k repos, ~400k grants, planted cycles/edge cases) — verified against a real container: exact row counts, a clean isolated depth-8 chain, 3 real cycles, an empty team, and a repo-beats-org case. Manifest of these written to `fixture_manifest.json` for later scripts to read directly.
- [x] `backend/app/cache.py` + `backend/tests/test_cache.py` (5 tests, all passing — written ahead of the later "backend/tests/" bullet since it made sense to test each module as it's built)
- [x] `backend/app/checker.py`: `check()` (R1) and `explain()` (R3), both done. `explain()` does its own fresh BFS per call (one query per level, no cache), reconstructs a readable chain (`"alice", "platform-eng", "infrastructure", "acme-corp (write)", "repo: api-gateway"`), and covers both deny shapes (no path at all vs. role too low) with a plain-English reason. Added `db.py` (connection pool) and `models.py` (`Role`, `ExplainResponse`, now depends on `pydantic`). 13 integration tests total against a real, separate, disposable test database, all passing, including two different cyclic-team cases actually terminating. Spot-checked both functions against the real fixture too — see decisions log below.
- [x] `backend/app/routes.py` (+ `backend/app/main.py`, not originally in the file list but something has to create the `FastAPI()` app) — `/check`, `/explain`, `POST /membership`, `DELETE /membership/{id}` wired to `invalidate_user`/`invalidate_all` — R2. New deps: fastapi, uvicorn, httpx (test-only). 6 route-level tests plus a live end-to-end pass against the real fixture over HTTP (curl), including proving a `POST /membership` immediately flips a `/check` result. Hit and fixed a real asyncpg/event-loop bug along the way — see decisions log.
- [x] rest of `backend/app/routes.py` — `/orgs`, `/orgs/{id}/repos`, `/resource/{id}`, `/resource/{id}/grants`, `/teams/{id}` (parent + children), `GET`/`POST /grant`. `GET /grant` requires `subject_id` — the table has ~400k rows, so no unfiltered dump; it's the mirror of `/resource/{id}/grants` (resource→grants vs subject→grants). 9 new tests (15 total in `test_routes.py`), all passing first try, plus a live spot-check against the real fixture (1000 orgs, a real team's parent/children, a subject's real grants).
- [x] `backend/app/oracle.py` — fully separate, no import from `checker.py`/`cache.py`, ~40 lines, one query per BFS hop (fine, not perf-critical). 8 tests (same scenarios as `TestCheck`, on purpose — they should agree on all of them). Also ran a direct 203-case comparison against the real fixture (real random users/repos + the deep-nesting and repo-beats-org manifest cases): 0 mismatches. That's a preview of what `oracle_harness.py` (next) formalizes properly with interleaved mutations.
- [x] `backend/scripts/oracle_harness.py` — R4. Ran 1k + 5k rounds against the real fixture (0 divergences), plus 14k targeted checks against every user under the fixture's 3 cyclic teams specifically (0 divergences). Then validated the comparison itself isn't just agreeing with everything: injected a real bug (disabled org-grant climbing) and confirmed 300/300 detection on real org-covers-repo cases. Full writeup with the "why zero is defensible" reasoning is in README's new "R4 — divergences found" section. Note: running the harness mutates the target database (that's the point) — regenerate the fixture afterward if you want a clean one back.
- [x] `backend/scripts/load_test.py` — CLI flags for check-rate/mutation-rate/duration/url, paces dispatch via httpx2, reports p50/p95/p99. Found and fixed 3 real bugs while validating it against the live server: (1) `_apply_mutation` had no exception handling, so one failed request crashed the whole run; (2) a genuine race condition in `db.py`'s lazy pool init -- a cold-start burst of concurrent requests all saw `_pool is None` at once and each independently called `create_pool()`, instantly blowing past Postgres's `max_connections`; fixed with an `asyncio.Lock`; (3) asyncpg's default pool `max_size=10` is too small at real load, now configurable via `DB_POOL_MAX_SIZE` (default 20). Added `backend/tests/test_db.py` as a regression test for bug (2) -- verified it actually discriminates by temporarily reverting the lock and confirming the test fails (50 pool creations instead of 1). Also found real, unresolved throughput instability (significant run-to-run variance, e.g. 100/sec measuring worse than 200/sec) that needs proper repeated-run characterization -- deferred to `BENCH.md`, not chased further here. Full writeup in README.
- [x] `backend/tests/` — already covered incrementally: `test_cache.py`, `test_checker.py`, `test_oracle.py`, `test_routes.py`, `test_db.py` (42 tests total, all passing)
- [x] `frontend/` — scaffolded with Vite (React + TS, no extra libraries -- matches the original "simple react, no library" call). `api.ts` (hand-written types matching the backend's Pydantic models -- FastAPI's OpenAPI schema could generate these instead, chose hand-written for a handful of endpoints), `CheckPanel.tsx`, `ExplainView.tsx`, `OrgBrowser.tsx`. Added CORS middleware to the backend (`main.py`) since the frontend calls it cross-origin. Known gap: no user search by name -- the API has no endpoint for it, so CheckPanel takes a numeric user id. `npm run build` is clean (no TS errors); verified every real API response shape matches the hand-written TS interfaces exactly against the live containerized backend, and confirmed the built JS bundle contains our actual component text, not demo boilerplate. Could not visually confirm rendering/interactivity -- no browser/screenshot tool in this environment; said so rather than claiming full UI verification.
- [x] `docker-compose.yml` + all three Dockerfiles, resource-limited: postgres 0.5 vCPU/512MB, backend 1.0 vCPU/1536MB, frontend 0.5 vCPU/2048MB (nginx serving the Vite build) -- totals exactly 2.0 vCPU/4096MB, matching the budget precisely. Fixed the non-idempotent fixture generator (found earlier during oracle harness testing) so the backend container can safely seed on every startup, including against a volume with existing data. Verified with a real clean-slate `docker compose down -v && docker compose up -d --build`: all three services start, fixture auto-seeds, backend responds correctly, frontend serves the real built app (not demo boilerplate -- checked the JS bundle content), restarting backend skips re-seeding, full 42-test suite passes against the containerized Postgres.
- [ ] `BENCH.md` with real numbers
- [ ] finish `README.md`: run instructions, current state, decisions log
- [ ] keep exporting agent transcripts as we go

## How we're working
Step by step, one reviewable piece at a time. No one-shotting the whole thing.

## Status
Design locked, written into README's tradeoffs section. File-by-file plan written.
Nothing built yet. Next: `backend/app/schema.sql`.
