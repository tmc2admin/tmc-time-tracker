import unittest
from datetime import datetime
from types import SimpleNamespace

import pytz

from app.helpers.calculations import calculate_entries_time_totals


def utc(hour, minute=0):
    return pytz.utc.localize(datetime(2026, 7, 27, hour, minute))


def interval(start_attr, end_attr, start, end, reason=None):
    return SimpleNamespace(**{
        start_attr: start,
        end_attr: end,
        'reason': reason
    })


def time_entry(start, end, breaks=None, idles=None):
    return SimpleNamespace(
        clock_in_time=start,
        clock_out_time=end,
        break_entries=breaks or [],
        idle_entries=idles or []
    )


class TimeCalculationTests(unittest.TestCase):
    def test_gross_excludes_gaps_between_sessions(self):
        entries = [
            time_entry(utc(8), utc(10)),
            time_entry(utc(11), utc(12))
        ]

        totals = calculate_entries_time_totals(entries, now_utc=utc(13))

        self.assertEqual(totals['gross_seconds'], 3 * 3600)
        self.assertEqual(totals['pause_seconds'], 0)
        self.assertEqual(totals['net_seconds'], 3 * 3600)

    def test_pause_merges_overlaps_and_excludes_meetings(self):
        breaks = [
            interval(
                'break_start_time',
                'break_end_time',
                utc(8, 30),
                utc(9)
            )
        ]
        idles = [
            interval(
                'idle_start_time',
                'idle_end_time',
                utc(8, 45),
                utc(9, 15)
            ),
            interval(
                'idle_start_time',
                'idle_end_time',
                utc(9, 15),
                utc(9, 30),
                reason='In Meeting'
            )
        ]
        entries = [time_entry(utc(8), utc(10), breaks=breaks, idles=idles)]

        totals = calculate_entries_time_totals(entries, now_utc=utc(11))

        self.assertEqual(totals['gross_seconds'], 2 * 3600)
        self.assertEqual(totals['break_seconds'], 30 * 60)
        self.assertEqual(totals['idle_seconds'], 30 * 60)
        self.assertEqual(totals['pause_seconds'], 45 * 60)
        self.assertEqual(totals['net_seconds'], 75 * 60)

    def test_open_intervals_are_clamped_to_session_and_report_window(self):
        breaks = [
            interval(
                'break_start_time',
                'break_end_time',
                utc(9, 30),
                None
            )
        ]
        entries = [time_entry(utc(8), None, breaks=breaks)]

        totals = calculate_entries_time_totals(
            entries,
            now_utc=utc(11),
            window_start=utc(9),
            window_end=utc(10)
        )

        self.assertEqual(totals['gross_seconds'], 60 * 60)
        self.assertEqual(totals['pause_seconds'], 30 * 60)
        self.assertEqual(totals['net_seconds'], 30 * 60)


if __name__ == '__main__':
    unittest.main()
