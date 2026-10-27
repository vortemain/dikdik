import datetime
import unittest

from dikdik.cron import CronError, next_after, parse


class CronTests(unittest.TestCase):
    def stamp(self, text):
        return datetime.datetime.fromisoformat(text).replace(tzinfo=datetime.timezone.utc).timestamp()

    def test_lists_ranges_steps_and_weekdays(self):
        rule = parse("*/15 9-17 * * 1-5")
        self.assertTrue(rule.matches(datetime.datetime(2026, 10, 27, 9, 30)))  # Tuesday
        self.assertFalse(rule.matches(datetime.datetime(2026, 10, 25, 9, 30)))  # Sunday
        self.assertFalse(rule.matches(datetime.datetime(2026, 10, 27, 9, 31)))
        self.assertEqual(parse("0 0 * * 7").weekday, frozenset({0}))

    def test_dom_and_weekday_use_standard_or_rule(self):
        rule = parse("0 8 1 * 1")
        self.assertTrue(rule.matches(datetime.datetime(2026, 12, 1, 8, 0)))  # first, Tuesday
        self.assertTrue(rule.matches(datetime.datetime(2026, 12, 7, 8, 0)))  # Monday, seventh
        self.assertFalse(rule.matches(datetime.datetime(2026, 12, 2, 8, 0)))

    def test_next_after_is_strict_and_time_zone_aware(self):
        start = self.stamp("2026-10-27T05:59:59+00:00")
        self.assertEqual(next_after("0 9 * * *", start, "Africa/Nairobi"), self.stamp("2026-10-27T06:00:00+00:00"))
        self.assertEqual(next_after("0 9 * * *", self.stamp("2026-10-27T06:00:00+00:00"), "Africa/Nairobi"), self.stamp("2026-10-28T06:00:00+00:00"))

    def test_invalid_expressions_are_plain_errors(self):
        for value in ("", "* * * *", "60 * * * *", "* 1-0 * * *", "*/0 * * * *", "* * * * monday"):
            with self.subTest(value=value), self.assertRaises(CronError):
                parse(value)
        with self.assertRaisesRegex(CronError, "Unknown time zone"):
            next_after("0 0 * * *", 0, "Moon/Sea")


if __name__ == "__main__":
    unittest.main()
