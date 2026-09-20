# Repository Access Service

## Running instructions
No frontend yet, and `docker-compose.yml` only runs Postgres so far -- the API has to
be run directly.

```bash
docker compose up -d postgres

# fixture data (real dataset, ~400k grants)
cd backend && pip install -r requirements.txt
DATABASE_URL="postgresql://repo_access:repo_access@localhost:5432/repo_access" \
  python scripts/generate_fixture.py

# run the API
DATABASE_URL="postgresql://repo_access:repo_access@localhost:5432/repo_access" \
  uvicorn app.main:app --reload
# then e.g. curl "http://127.0.0.1:8000/check?user_id=1&action=read&repo_id=100"

# tests -- needs a SEPARATE database, since tests truncate tables on every run
createdb -h localhost -p 5432 -U repo_access repo_access_test   # PGPASSWORD=repo_access
psql -h localhost -p 5432 -U repo_access -d repo_access_test -f app/schema.sql
python3 -m unittest tests.test_cache tests.test_checker tests.test_routes -v
```

## Current state
Built: DB schema, fixture generator (with planted cycles/edge cases), the in-memory
cache, `checker.check()` / `checker.explain()` (R1 + R3), and the full API (`/check`,
`/explain`, membership + grant mutations, browse endpoints) -- all with passing tests,
plus live end-to-end passes over real HTTP against the real fixture.

Not built yet: the oracle and R4 comparison harness, the load generator, the frontend,
and the backend/frontend Dockerfiles. `docker-compose.yml` currently only runs
Postgres.

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
  never string-built into the query itself. This applies to the oracle too, once built.
- The only f-strings touching a query anywhere in the backend build display text or
  error messages (e.g. `"No resource with id {resource_id}"`), never SQL.
- No user input is ever eligible for SQL injection here, by construction, not by
  review -- there's simply no code path where a request value gets concatenated into
  a query string.

## Decisions

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
