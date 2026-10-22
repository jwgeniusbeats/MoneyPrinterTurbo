import unittest

from app.utils import utils


class TestTimeConvertSecondsToHmsm(unittest.TestCase):
    def test_float_boundary_is_not_truncated(self):
        # 2.01 * 1000 == 2009.999... used to truncate to 009
        self.assertEqual(utils.time_convert_seconds_to_hmsm(2.01), "00:00:02,010")

    def test_rounding_carries_into_seconds_and_minutes(self):
        self.assertEqual(utils.time_convert_seconds_to_hmsm(59.9996), "00:01:00,000")

    def test_hours(self):
        self.assertEqual(utils.time_convert_seconds_to_hmsm(3723.5), "01:02:03,500")


if __name__ == "__main__":
    unittest.main()
