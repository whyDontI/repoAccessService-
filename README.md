# Repository Access Service

## Running instructions
So far, only the DB layer and `check()`/`explain()` exist — no API or frontend yet.

```bash
docker compose up -d postgres

# fixture data (real dataset, ~400k grants)
cd backend && pip install -r requirements.txt
DATABASE_URL="postgresql://repo_access:repo_access@localhost:5432/repo_access" \
  python scripts/generate_fixture.py

# tests -- needs a SEPARATE database, since tests truncate tables on every run
createdb -h localhost -p 5432 -U repo_access repo_access_test   # PGPASSWORD=repo_access
psql -h localhost -p 5432 -U repo_access -d repo_access_test -f app/schema.sql
python3 -m unittest tests.test_cache tests.test_checker -v
```

## Current state
Built: DB schema, fixture generator (with planted cycles/edge cases), the in-memory
cache, and `checker.check()` / `checker.explain()` (R1 + R3), all with passing tests.

Not built yet: the API layer (`routes.py`), the oracle and R4 comparison harness, the
load generator, the frontend, and the backend/frontend Dockerfiles. `docker-compose.yml`
currently only runs Postgres.

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
