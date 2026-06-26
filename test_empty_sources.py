import os
import tempfile
import unittest

from monitor import State, checked_products


class EmptySourceTests(unittest.TestCase):
    def test_spielwaren_kor_sources_can_be_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            state = State(os.path.join(directory, "state.db"))
            self.assertEqual(checked_products("spielwaren-pokemon-kor", [], state), [])
            self.assertEqual(checked_products("spielwaren-onepiece-kor", [], state), [])

    def test_non_allowed_source_still_fails_safe_on_empty_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            state = State(os.path.join(directory, "state.db"))
            with self.assertRaisesRegex(ValueError, "returned zero products"):
                checked_products("crazycards-pokemon", [], state)


if __name__ == "__main__":
    unittest.main()
