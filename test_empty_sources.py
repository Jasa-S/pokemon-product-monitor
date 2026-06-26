import os
import tempfile
import unittest

from monitor import State, checked_products


class EmptySourceTests(unittest.TestCase):
    def test_zero_product_result_is_valid_for_current_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            state = State(os.path.join(directory, "state.db"))
            self.assertEqual(checked_products("spielwaren-pokemon-kor", [], state), [])
            self.assertEqual(checked_products("spielwaren-onepiece-kor", [], state), [])
            self.assertEqual(checked_products("crazycards-pokemon", [], state), [])
            self.assertEqual(checked_products("crazycards-onepiece", [], state), [])


if __name__ == "__main__":
    unittest.main()
