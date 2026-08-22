from datetime import datetime
import unittest

from obs_match_recorder.scheduler import scheduled_start_time


class ScheduledStartTimeTests(unittest.TestCase):
    def test_subtracts_advance_minutes(self) -> None:
        result = scheduled_start_time("2026-08-20 20:00", "15")
        self.assertEqual(result, datetime(2026, 8, 20, 19, 45))


if __name__ == "__main__":
    unittest.main()
