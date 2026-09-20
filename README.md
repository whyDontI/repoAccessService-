# Repository Access Service

## Running instructions
Not written yet.

## Current state
Nothing built yet.

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
Not written yet. Will log what was delegated to the AI agent vs. done directly, where
the agent got something wrong, and anything overridden — tied to commits.
