import os
import sys
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(__file__))
import watchdog

NY = ZoneInfo("America/New_York")


def ny(y, mo, d, h, mi, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=NY)


class TestWatchdog(unittest.TestCase):
    def test_on_time_is_quiet(self):
        self.assertIsNone(watchdog.evaluate(ny(2026, 10, 16, 12, 40), ny(2026, 10, 16, 12, 30, 32)))

    def test_late_lists_missed_slots(self):
        r = watchdog.evaluate(ny(2026, 10, 16, 12, 40), ny(2026, 10, 16, 11, 30, 30))
        self.assertEqual(r[0], "Trigger check is late")
        self.assertEqual(r[2], "high")
        self.assertIn("12:00, 12:30", r[1])
        self.assertIn("69 min ago", r[1])

    def test_grace_period(self):
        # 12:37 is under 8 minutes after the 12:30 slot, so only 12:00 counts as missed
        r = watchdog.evaluate(ny(2026, 10, 16, 12, 37), ny(2026, 10, 16, 11, 30, 30))
        self.assertIn("Missed: 12:00.", r[1])

    def test_no_check_at_the_open(self):
        r = watchdog.evaluate(ny(2026, 10, 16, 9, 40), ny(2026, 10, 15, 16, 0, 20))
        self.assertIn("Missed: 09:30.", r[1])
        self.assertIn("Thu 16:00", r[1])

    def test_never_checked(self):
        r = watchdog.evaluate(ny(2026, 10, 16, 10, 40), None)
        self.assertEqual(r[0], "Trigger check is late")
        self.assertIn("never", r[1])

    def test_pre_open(self):
        self.assertIsNone(watchdog.evaluate(ny(2026, 10, 16, 9, 15), ny(2026, 10, 15, 16, 0, 30)))
        r = watchdog.evaluate(ny(2026, 10, 16, 9, 15), ny(2026, 10, 15, 14, 0, 30))
        self.assertEqual(r[0], "Trigger check problem before the open")

    def test_monday_pre_open_looks_at_friday(self):
        self.assertIsNone(watchdog.evaluate(ny(2026, 10, 19, 9, 15), ny(2026, 10, 16, 16, 0, 20)))

    def test_day_after_thanksgiving_looks_at_wednesday(self):
        self.assertIsNone(watchdog.evaluate(ny(2026, 11, 27, 9, 15), ny(2026, 11, 25, 16, 0, 20)))

    def test_four_oclock_check_is_not_due_until_grace(self):
        # 16:05 is under 8 minutes after the 16:00 slot, so a 15:30 check is still on time
        self.assertIsNone(watchdog.evaluate(ny(2026, 10, 16, 16, 5), ny(2026, 10, 16, 15, 30, 20)))

    def test_post_close(self):
        ok = watchdog.evaluate(ny(2026, 10, 16, 16, 10), ny(2026, 10, 16, 16, 0, 20))
        self.assertEqual((ok[0], ok[2]), ("Watchdog: all good", "min"))
        bad = watchdog.evaluate(ny(2026, 10, 16, 16, 10), ny(2026, 10, 16, 14, 0, 20))
        self.assertEqual((bad[0], bad[2]), ("Trigger check missed today", "high"))

    def test_weekend_and_holiday_are_silent(self):
        stale = ny(2026, 10, 9, 15, 30)
        self.assertIsNone(watchdog.evaluate(ny(2026, 10, 17, 10, 40), stale))   # Saturday
        self.assertIsNone(watchdog.evaluate(ny(2026, 11, 26, 10, 40), stale))   # Thanksgiving
        self.assertIsNone(watchdog.evaluate(ny(2026, 12, 25, 16, 10), stale))    # Christmas

    def test_outside_windows_are_silent(self):
        stale = ny(2026, 10, 9, 15, 30)
        self.assertIsNone(watchdog.evaluate(ny(2026, 10, 16, 8, 15), stale))
        self.assertIsNone(watchdog.evaluate(ny(2026, 10, 16, 18, 30), stale))

    def test_winter_time(self):
        self.assertIsNone(watchdog.evaluate(ny(2026, 12, 2, 12, 40), ny(2026, 12, 2, 12, 30, 15)))
        self.assertIsNotNone(watchdog.evaluate(ny(2026, 12, 2, 12, 40), ny(2026, 12, 2, 11, 30, 15)))


if __name__ == "__main__":
    unittest.main()
