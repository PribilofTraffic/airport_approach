"""The zone label printed after local times, and reading those times back.

Run from the repository folder:  python3 -m unittest discover -s tests
"""

import os
import sys
import unittest
from datetime import datetime

import pytz

REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, REPO)
os.environ.setdefault('AIRPORTS_FILE', os.path.join(REPO, 'airports.json'))
import config  # noqa: E402

AK = pytz.timezone('America/Anchorage')
UTC = pytz.utc


def at(year, month, day, hour, minute=0):
    """A UTC moment, shown in Alaska time."""
    return datetime(year, month, day, hour, minute, tzinfo=UTC).astimezone(AK)


class TestAutoLabel(unittest.TestCase):

    def setUp(self):
        self.saved = (config.TIME_LABEL, config.AUTO_LABEL)
        config.TIME_LABEL, config.AUTO_LABEL = 'auto', True

    def tearDown(self):
        config.TIME_LABEL, config.AUTO_LABEL = self.saved

    def test_the_shipped_config_is_auto(self):
        self.assertEqual(self.saved, ('auto', True))

    def test_winter_says_standard_time(self):
        self.assertEqual(config.stamp(at(2026, 11, 19, 23, 0)), '2026-11-19 02:00 PM AKST')
        self.assertEqual(config.clock(at(2026, 11, 19, 23, 0)), '02:00 PM AKST')

    def test_summer_says_daylight_time(self):
        """The bug this replaces: the same moment used to print
        '2026-07-04 02:00 PM AKST', an hour wrong for anyone who believed
        the label."""
        self.assertEqual(config.stamp(at(2026, 7, 4, 22, 0)), '2026-07-04 02:00 PM AKDT')

    def test_every_stamp_reads_back_to_the_same_moment(self):
        for moment in (at(2026, 1, 15, 12), at(2026, 7, 4, 22), at(2026, 3, 8, 10, 59),
                       at(2026, 3, 8, 11, 0), at(2026, 11, 1, 9, 30), at(2026, 11, 1, 10, 30)):
            self.assertEqual(config.parse_stamp(config.stamp(moment), AK), moment, config.stamp(moment))

    def test_the_hour_that_happens_twice_is_told_apart_by_its_label(self):
        first = config.parse_stamp('2026-11-01 01:30 AM AKDT', AK)
        second = config.parse_stamp('2026-11-01 01:30 AM AKST', AK)
        self.assertEqual((second - first).total_seconds(), 3600)

    def test_a_stamp_from_the_old_fixed_label_still_reads_back(self):
        """Files written last summer say AKST on daylight-time digits. The
        digits were always right, so they are read as the local time they
        show."""
        old = config.parse_stamp('2026-07-04 02:00 PM AKST', AK)
        self.assertEqual(old, at(2026, 7, 4, 22, 0))

    def test_a_stamp_with_no_label_or_a_strange_one_reads_back(self):
        self.assertEqual(config.parse_stamp('2026-11-19 02:00 PM', AK), at(2026, 11, 19, 23, 0))
        self.assertEqual(config.parse_stamp('2026-11-19 02:00 PM ET', AK), at(2026, 11, 19, 23, 0))

    def test_rubbish_is_refused(self):
        for text in ('', 'yesterday', None, 5, '2026-11-19'):
            with self.assertRaises(ValueError):
                config.parse_stamp(text, AK)

    def test_a_zone_without_daylight_time_never_changes_label(self):
        """What Alaska would look like if daylight saving time went away:
        the label stops changing because the zone's rules say so, with no
        change here."""
        phoenix = pytz.timezone('America/Phoenix')
        winter = datetime(2026, 1, 15, 19, 0, tzinfo=UTC).astimezone(phoenix)
        summer = datetime(2026, 7, 15, 19, 0, tzinfo=UTC).astimezone(phoenix)
        self.assertEqual(config.stamp(winter), '2026-01-15 12:00 PM MST')
        self.assertEqual(config.stamp(summer), '2026-07-15 12:00 PM MST')


class TestFixedLabel(unittest.TestCase):

    def test_fixed_text_is_printed_as_given_all_year(self):
        saved = (config.TIME_LABEL, config.AUTO_LABEL)
        config.TIME_LABEL, config.AUTO_LABEL = 'ET', False
        try:
            self.assertEqual(config.stamp(at(2026, 7, 4, 22, 0)), '2026-07-04 02:00 PM ET')
            self.assertEqual(config.parse_stamp('2026-07-04 02:00 PM ET', AK), at(2026, 7, 4, 22, 0))
        finally:
            config.TIME_LABEL, config.AUTO_LABEL = saved


if __name__ == '__main__':
    unittest.main()
