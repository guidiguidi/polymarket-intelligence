import unittest
import time
import math


class TestPolymarketPipeline(unittest.TestCase):
    def test_evm_address_validation(self):
        valid_evm = "0x71C841609077982772594a32338A959cD1696E37"
        invalid_evm = "0x1234"

        def is_valid_evm(addr):
            return addr.startswith("0x") and len(addr) == 42 and all(c in "0123456789abcdefABCDEF" for c in addr[2:])

        self.assertTrue(is_valid_evm(valid_evm))
        self.assertFalse(is_valid_evm(invalid_evm))

    def test_exponential_backoff_calculation(self):
        def calculate_backoff(attempt, base_delay=1.0, max_delay=30.0):
            return min(max_delay, base_delay * (2 ** attempt))

        self.assertEqual(calculate_backoff(0), 1.0)
        self.assertEqual(calculate_backoff(1), 2.0)
        self.assertEqual(calculate_backoff(2), 4.0)
        self.assertEqual(calculate_backoff(3), 8.0)
        self.assertEqual(calculate_backoff(10), 30.0)

    def test_pagination_offset(self):
        limit = 50
        page = 3
        offset = page * limit
        self.assertEqual(offset, 150)


if __name__ == "__main__":
    unittest.main()
