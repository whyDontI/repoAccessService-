<InitialInstructions>
# This is a 4-hour take-home assignment.
-- Detailed documents are under Documents directory, this my brain dump
# We need to Optimize for readable, minimal, defensible code
# No need to over-engineer a simple solution

Rules:

- Never add an abstraction (interface, factory, plugin system, config layer)
  used only once. If you think you need one, stop and ask me.
- Prefer the direct implementation over flexible.
- No new dependency unless I explicitly ask. Standard library first.
- Every file should be HUMAN READABLE start to end in under 2 minutes.
- Never touch a file I didn't ask you to touch.
- After writing code, briefly explain what each function does and why it
  exists, so I can catch anything I wouldn't be able to defend myself.
- If a requirement is ambiguous, stop and ask, don't guess and proceed.
  </InitialInstructions>

<BrainDump>

<About>
# Repository Access Service
- It's a service to check whether user has access to a certain repository or not, plus what kind of access? read, write, admin, owner. Higher roles imply lower ones.
- We'll need a docker compose file (yml), we have strict limit of 2 vCpus and 4GB RAM
- It should handle 5k check every sec
- And Check latency p95 should be under 10ms always
- We need to generate fake data of 50k Users, 4k teams nested up to 8 levels, 20k repos and 400k grants.
- Also a script to test the load, running 5k checks per second and separately applying grant mutations at a configurable rate.
- If the someone's access is removed, it should reflect in the api response in under 1 second
- Every answer should have an explanation
- The hierarchy is, a User belongs to a Team, and a Team may also belong in a Team, the Team that's at the top we can have an empty parent for it
While querying we may need to keep a visited list to avoid infinite loops
</About>

<Architecture>
- We need to build this in Python, and React TS for frontend
- I am thinking simple react no library for frontend since there isn't any complex logic on frontend that'll need any complex React features
- For the backend lets use Python with Fast API, pydantic for model definitions / types, and Postgres as DB.
- Let's run only one instance of backend, it'll be easier to use in-memory cache with it
- We'll need an in memory cache, a simple python dict with set, get, invalidate_(user/team) and invalidate_all functions
</Architecture>

<DBTables>
- We'll have following tables
- Subject - to store user and teams
-- id
-- type (user / team)
-- name

- resources
  -- id
  -- name
  -- type (org / repo)
  -- org_id (parent org id for repos / empty for orgs)
  -- name

- team_relationships
  -- id
  -- subject_id (user or team)
  -- belongs_to_team_id (a valid team_id OR Empty for parent team)

- grants
  -- id
  -- subject_id
  -- role
  -- resource_id

Based on our queries we can index tables

- team_relationship
  -- Index on subject_id and belongs_to_team_id

- Grants
  -- Index on Subject_Id and resource_id
  </DBTables>

<CodeStructure>
- We need an enum for Role - Read, Write, Admin, Owner
-------------------
- We need a cache service, a simple cache.py file, exporting below functions
-- get
-- set
-- invalidate_all
-- invalidate_user

-- The cache will have a subject_id as key and json value with path and created_at timestamp
-- subject_id: { path: [subject_id, subject_id, subject_id, , subject_id, resource_id, resource_id], created_at: <date>}
-- Denoting user_id->team_id->team_id->team_id

- The plan is to invalidate user cache when anything for a user is changed
- When something is changed for a team, we'll delete entire cache, because I am expecting team level changes will be rare

---

- checker.py
- function check(user_id, action, repo_id)
  -- Action is same as the role enum
  -- This function will check the cache for users path
  -- Cache Miss
  --- recursively query team_relationship DB with a visited list

-- Query the grant table to find all the grants for the subject->resource pair
-- Pick the highest and check against the action if >= action return True or False

---

API Routes

- GET /check - Returns boolean
- GET /explain - Returns entire user path with Boolean (has access or not)
- GET /orgs
- GET /orgs/{id}/repos
- GET /resource/{id}
- GET + POST /grant - assign grant subject - resource pair

---

- A script to generate the test data as explained before
- A script to test load on the system, if we can run it as a terminal command with flags, it'll be great
  -- the load script should run both /check and /grant at the same time to test real world scenario

---

TESTS

- Simple minimal unit tests for each function with actual tests no mocking
- No tests on Frontend for now, maybe later once it's built

---

FrontEnd

- A Section with a repo / org dropdown + user search (50k not easy to load in a dropdown) + action dropdown and a check button to hit /check api endpoint
  -- We need to show the answer with last updated timestamp (if cache hit)
  -- Make sure the API request has proper headers to avoid caching in the browser
- To show the nestings I am not sure what to use - you suggest

---

Docker

- Docker compose up should start everything

</CodeStructure>

</BrainDump>
