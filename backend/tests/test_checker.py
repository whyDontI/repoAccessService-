"""Real Postgres, no mocking. Needs TEST_DATABASE_URL (or the default
below) pointing at an empty database with schema.sql applied -- see
README's running instructions. Every table is truncated before each test,
so this must never point at the dev/fixture database.
"""
import os
import unittest

import asyncpg

from app import cache, checker, db
from app.models import Role

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://repo_access:repo_access@localhost:5432/repo_access_test",
)


class TestCheck(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        db._pool = await asyncpg.create_pool(TEST_DATABASE_URL)
        async with db._pool.acquire() as conn:
            await conn.execute(
                "TRUNCATE grants, team_relationships, resources, subjects RESTART IDENTITY CASCADE"
            )
        cache.invalidate_all()

    async def asyncTearDown(self):
        await db._pool.close()

    async def _seed(self, subjects=(), resources=(), team_edges=(), grants=()):
        pool = await db.get_pool()
        async with pool.acquire() as conn:
            for id_, type_, name in subjects:
                await conn.execute(
                    "INSERT INTO subjects (id, type, name) VALUES ($1, $2, $3)",
                    id_, type_, name,
                )
            for id_, type_, org_id, name in resources:
                await conn.execute(
                    "INSERT INTO resources (id, type, org_id, name) VALUES ($1, $2, $3, $4)",
                    id_, type_, org_id, name,
                )
            for subject_id, parent_id in team_edges:
                await conn.execute(
                    "INSERT INTO team_relationships (subject_id, belongs_to_team_id) VALUES ($1, $2)",
                    subject_id, parent_id,
                )
            for subject_id, role, resource_id in grants:
                await conn.execute(
                    "INSERT INTO grants (subject_id, role, resource_id) VALUES ($1, $2, $3)",
                    subject_id, role, resource_id,
                )

    async def test_direct_grant_on_repo(self):
        await self._seed(
            subjects=[(1, "user", "alice")],
            resources=[(900, "org", None, "acme"), (100, "repo", 900, "api")],
            grants=[(1, "read", 100)],
        )
        self.assertTrue(await checker.check(1, Role.READ, 100))
        self.assertFalse(await checker.check(1, Role.WRITE, 100))

    async def test_grant_inherited_through_one_team(self):
        await self._seed(
            subjects=[(2, "user", "bob"), (200, "team", "platform")],
            resources=[(900, "org", None, "acme"), (100, "repo", 900, "api")],
            team_edges=[(2, 200)],
            grants=[(200, "write", 100)],
        )
        self.assertTrue(await checker.check(2, Role.WRITE, 100))
        self.assertFalse(await checker.check(2, Role.ADMIN, 100))

    async def test_grant_inherited_through_nested_teams(self):
        await self._seed(
            subjects=[(3, "user", "carol"), (300, "team", "infra"), (301, "team", "platform")],
            resources=[(900, "org", None, "acme"), (100, "repo", 900, "api")],
            team_edges=[(3, 300), (300, 301)],
            grants=[(301, "admin", 100)],
        )
        self.assertTrue(await checker.check(3, Role.ADMIN, 100))

    async def test_org_grant_covers_its_repo(self):
        await self._seed(
            subjects=[(4, "user", "dave")],
            resources=[(900, "org", None, "acme"), (100, "repo", 900, "api")],
            grants=[(4, "write", 900)],
        )
        self.assertTrue(await checker.check(4, Role.WRITE, 100))

    async def test_repo_grant_beats_org_grant(self):
        await self._seed(
            subjects=[(5, "user", "erin")],
            resources=[(900, "org", None, "acme"), (100, "repo", 900, "api")],
            grants=[(5, "read", 900), (5, "owner", 100)],
        )
        self.assertTrue(await checker.check(5, Role.OWNER, 100))

    async def test_deny_when_no_path_to_resource(self):
        await self._seed(
            subjects=[(6, "user", "frank")],
            resources=[(900, "org", None, "acme"), (100, "repo", 900, "api")],
        )
        self.assertFalse(await checker.check(6, Role.READ, 100))

    async def test_deny_when_role_too_low(self):
        await self._seed(
            subjects=[(7, "user", "grace")],
            resources=[(900, "org", None, "acme"), (100, "repo", 900, "api")],
            grants=[(7, "read", 100)],
        )
        self.assertFalse(await checker.check(7, Role.ADMIN, 100))

    async def test_cyclic_teams_do_not_hang(self):
        await self._seed(
            subjects=[(8, "user", "heidi"), (800, "team", "a"), (801, "team", "b")],
            resources=[(900, "org", None, "acme"), (100, "repo", 900, "api")],
            team_edges=[(8, 800), (800, 801), (801, 800)],  # 800 <-> 801 cycle
            grants=[(801, "read", 100)],
        )
        self.assertTrue(await checker.check(8, Role.READ, 100))


class TestExplain(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        db._pool = await asyncpg.create_pool(TEST_DATABASE_URL)
        async with db._pool.acquire() as conn:
            await conn.execute(
                "TRUNCATE grants, team_relationships, resources, subjects RESTART IDENTITY CASCADE"
            )
        cache.invalidate_all()

    async def asyncTearDown(self):
        await db._pool.close()

    _seed = TestCheck._seed

    async def test_allow_chain_through_nested_teams(self):
        await self._seed(
            subjects=[
                (1, "user", "alice"),
                (200, "team", "platform-eng"),
                (201, "team", "infrastructure"),
            ],
            resources=[(900, "org", None, "acme-corp"), (100, "repo", 900, "api-gateway")],
            team_edges=[(1, 200), (200, 201)],
            grants=[(201, "write", 900)],
        )
        result = await checker.explain(1, Role.WRITE, 100)
        self.assertTrue(result.allowed)
        self.assertEqual(result.granted_role, Role.WRITE)
        self.assertIsNone(result.reason)
        self.assertEqual(
            result.chain,
            ["alice", "platform-eng", "infrastructure", "acme-corp (write)", "repo: api-gateway"],
        )

    async def test_allow_chain_on_direct_repo_grant_has_no_duplicate_repo_step(self):
        await self._seed(
            subjects=[(2, "user", "bob")],
            resources=[(900, "org", None, "acme-corp"), (100, "repo", 900, "api-gateway")],
            grants=[(2, "owner", 100)],
        )
        result = await checker.explain(2, Role.OWNER, 100)
        self.assertTrue(result.allowed)
        self.assertEqual(result.chain, ["bob", "api-gateway (owner)"])

    async def test_deny_reason_when_role_too_low(self):
        await self._seed(
            subjects=[(3, "user", "carol")],
            resources=[(900, "org", None, "acme-corp"), (100, "repo", 900, "api-gateway")],
            grants=[(3, "read", 100)],
        )
        result = await checker.explain(3, Role.ADMIN, 100)
        self.assertFalse(result.allowed)
        self.assertEqual(result.granted_role, Role.READ)
        self.assertEqual(result.reason, "Highest role found is read, but admin was requested.")

    async def test_deny_reason_when_no_path_at_all(self):
        await self._seed(
            subjects=[(4, "user", "dave")],
            resources=[(900, "org", None, "acme-corp"), (100, "repo", 900, "api-gateway")],
        )
        result = await checker.explain(4, Role.READ, 100)
        self.assertFalse(result.allowed)
        self.assertIsNone(result.granted_role)
        self.assertEqual(result.chain, ["dave"])

    async def test_cyclic_teams_do_not_hang(self):
        await self._seed(
            subjects=[(5, "user", "erin"), (500, "team", "a"), (501, "team", "b")],
            resources=[(900, "org", None, "acme-corp"), (100, "repo", 900, "api-gateway")],
            team_edges=[(5, 500), (500, 501), (501, 500)],  # 500 <-> 501 cycle
            grants=[(501, "read", 100)],
        )
        result = await checker.explain(5, Role.READ, 100)
        self.assertTrue(result.allowed)


if __name__ == "__main__":
    unittest.main()
