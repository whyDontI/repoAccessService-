"""Real dict, no mocking -- there's nothing here worth mocking."""
import unittest

from app import cache


class TestCache(unittest.TestCase):
    def setUp(self):
        cache.invalidate_all()

    def test_miss_returns_none(self):
        self.assertIsNone(cache.get(1))

    def test_set_then_get_returns_the_same_team_set(self):
        cache.set(1, {10, 20, 30})
        team_ids, cached_at = cache.get(1)
        self.assertEqual(team_ids, {10, 20, 30})
        self.assertIsNotNone(cached_at)

    def test_invalidate_user_clears_only_that_user(self):
        cache.set(1, {10})
        cache.set(2, {20})
        cache.invalidate_user(1)
        self.assertIsNone(cache.get(1))
        self.assertIsNotNone(cache.get(2))

    def test_invalidate_user_on_absent_user_is_a_no_op(self):
        cache.invalidate_user(999)  # should not raise

    def test_invalidate_all_clears_everyone(self):
        cache.set(1, {10})
        cache.set(2, {20})
        cache.invalidate_all()
        self.assertIsNone(cache.get(1))
        self.assertIsNone(cache.get(2))


if __name__ == "__main__":
    unittest.main()
