import os
import tempfile
import unittest

from monitor import LIVE_SOURCES, State, checked_products


class EmptySourceTests(unittest.TestCase):
    def test_zero_product_result_is_valid_for_current_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            state = State(os.path.join(directory, "state.db"))
            for source in LIVE_SOURCES:
                with self.subTest(source=source):
                    self.assertEqual(checked_products(source, [], state), [])


if __name__ == "__main__":
    unittest.main()
