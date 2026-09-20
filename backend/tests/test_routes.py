"""Real Postgres, no mocking -- same TEST_DATABASE_URL setup as
test_checker.py.

asyncpg pools are bound to the event loop that created them, and
FastAPI's TestClient runs requests in its own loop (kept alive for the
`with` block below). So we never pre-create db._pool ourselves here --
we only point db.DATABASE_URL at the test database and let the app
create its pool lazily, the first time a request actually needs it,
inside TestClient's loop. Anything we need to do to the database
directly (seeding, inspecting) uses its own disposable connection
instead of the shared pool, for the same reason.
"""
import asyncio
import os
import unittest

import asyncpg
from fastapi.testclient import TestClient

from app import cache, db
from app.main import app

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://repo_access:repo_access@localhost:5432/repo_access_test",
)


async def _run_sql(*statements):
    conn = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        for statement in statements:
            await conn.execute(statement)
    finally:
        await conn.close()


class TestRoutes(unittest.TestCase):
    def setUp(self):
        asyncio.run(
            _run_sql(
                "TRUNCATE grants, team_relationships, resources, subjects RESTART IDENTITY CASCADE",
                "INSERT INTO subjects (id, type, name) VALUES "
                "(1, 'user', 'alice'), (200, 'team', 'platform'), (201, 'team', 'infra')",
                "INSERT INTO team_relationships (subject_id, belongs_to_team_id) VALUES (201, 200)",
                "INSERT INTO resources (id, type, org_id, name) VALUES "
                "(900, 'org', NULL, 'acme'), (100, 'repo', 900, 'api'), "
                "(901, 'org', NULL, 'other-org'), (101, 'repo', 901, 'other-repo')",
                "INSERT INTO grants (subject_id, role, resource_id) VALUES (200, 'write', 100)",
            )
        )
        db._pool = None
        db.DATABASE_URL = TEST_DATABASE_URL
        cache.invalidate_all()
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self):
        # Triggers the app's lifespan shutdown, which closes db._pool
        # itself -- inside the same loop it was created in. Closing it
        # from a separate asyncio.run() here can't work: asyncpg pools
        # are bound to the loop that created them, and that loop is
        # TestClient's, not this one.
        self.client.__exit__(None, None, None)
        db._pool = None

    def _check(self):
        return self.client.get(
            "/check", params={"user_id": 1, "action": "write", "repo_id": 100}
        )

    def test_check_denies_before_membership(self):
        resp = self._check()
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["allowed"])

    def test_membership_then_check_allows(self):
        self._check()  # warm the cache with a miss first
        resp = self.client.post(
            "/membership", json={"subject_id": 1, "belongs_to_team_id": 200}
        )
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(self._check().json()["allowed"])

    def test_removing_membership_revokes_access(self):
        self.client.post("/membership", json={"subject_id": 1, "belongs_to_team_id": 200})
        self.assertTrue(self._check().json()["allowed"])

        membership_id = asyncio.run(self._fetch_membership_id())
        resp = self.client.delete(f"/membership/{membership_id}")
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(self._check().json()["allowed"])

    async def _fetch_membership_id(self):
        conn = await asyncpg.connect(TEST_DATABASE_URL)
        try:
            row = await conn.fetchrow("SELECT id FROM team_relationships WHERE subject_id = 1")
            return row["id"]
        finally:
            await conn.close()

    def test_unknown_action_is_400(self):
        resp = self.client.get(
            "/check", params={"user_id": 1, "action": "superadmin", "repo_id": 100}
        )
        self.assertEqual(resp.status_code, 400)

    def test_membership_for_unknown_subject_is_404(self):
        resp = self.client.post(
            "/membership", json={"subject_id": 9999, "belongs_to_team_id": 200}
        )
        self.assertEqual(resp.status_code, 404)

    def test_explain_route_returns_chain(self):
        self.client.post("/membership", json={"subject_id": 1, "belongs_to_team_id": 200})
        resp = self.client.get(
            "/explain", params={"user_id": 1, "action": "write", "repo_id": 100}
        )
        self.assertEqual(resp.json()["chain"], ["alice", "platform", "api (write)"])

    def test_list_orgs(self):
        resp = self.client.get("/orgs")
        names = {org["name"] for org in resp.json()}
        self.assertEqual(names, {"acme", "other-org"})

    def test_list_org_repos_is_scoped_to_that_org(self):
        resp = self.client.get("/orgs/900/repos")
        self.assertEqual([repo["name"] for repo in resp.json()], ["api"])

    def test_get_resource(self):
        resp = self.client.get("/resource/100")
        self.assertEqual(resp.json(), {"id": 100, "name": "api", "type": "repo", "org_id": 900})

    def test_get_resource_404(self):
        resp = self.client.get("/resource/99999")
        self.assertEqual(resp.status_code, 404)

    def test_get_resource_grants(self):
        resp = self.client.get("/resource/100/grants")
        body = resp.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["subject_id"], 200)
        self.assertEqual(body[0]["role"], 2)  # Role.WRITE

    def test_get_team_shows_parent_and_children(self):
        resp = self.client.get("/teams/201")
        body = resp.json()
        self.assertEqual(body["parent"], {"id": 200, "name": "platform"})
        self.assertEqual(body["children"], [])

        resp = self.client.get("/teams/200")
        body = resp.json()
        self.assertIsNone(body["parent"])
        self.assertEqual(body["children"], [{"id": 201, "name": "infra"}])

    def test_get_team_404_for_a_user_id(self):
        resp = self.client.get("/teams/1")  # 1 is alice, a user, not a team
        self.assertEqual(resp.status_code, 404)

    def test_list_grants_requires_subject_id(self):
        resp = self.client.get("/grant", params={"subject_id": 200})
        body = resp.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["resource_id"], 100)

    def test_create_grant_then_it_shows_up_in_check(self):
        resp = self.client.post(
            "/grant", json={"subject_id": 1, "role": 1, "resource_id": 101}
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["role"], 1)

        resp = self.client.get(
            "/check", params={"user_id": 1, "action": "read", "repo_id": 101}
        )
        self.assertTrue(resp.json()["allowed"])


if __name__ == "__main__":
    unittest.main()
