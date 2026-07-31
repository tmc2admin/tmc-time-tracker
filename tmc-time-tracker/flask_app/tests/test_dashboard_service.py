import os
import unittest
from datetime import date, datetime

os.environ['SECRET_KEY'] = 'dashboard-service-test'
os.environ['DATABASE_URL'] = 'sqlite://'

from app import create_app, db
from app.models import BreakEntry, IdleEntry, TimeEntry, User
from app.services.web_service import WebService


class DashboardServiceTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()

        user = User(
            username='Dashboard User',
            email='dashboard@tm-connect.de',
            microsoft_oid='dashboard-oid'
        )
        db.session.add(user)
        db.session.flush()
        self.user_id = user.id

        morning = TimeEntry(
            user_id=user.id,
            clock_in_time=datetime(2026, 7, 27, 6, 0),
            clock_out_time=datetime(2026, 7, 27, 8, 0)
        )
        afternoon = TimeEntry(
            user_id=user.id,
            clock_in_time=datetime(2026, 7, 27, 9, 0),
            clock_out_time=datetime(2026, 7, 27, 10, 0)
        )
        db.session.add_all([morning, afternoon])
        db.session.flush()
        db.session.add_all([
            BreakEntry(
                time_entry_id=morning.id,
                break_start_time=datetime(2026, 7, 27, 6, 30),
                break_end_time=datetime(2026, 7, 27, 6, 45)
            ),
            IdleEntry(
                time_entry_id=morning.id,
                idle_start_time=datetime(2026, 7, 27, 7, 0),
                idle_end_time=datetime(2026, 7, 27, 7, 5)
            ),
            IdleEntry(
                time_entry_id=morning.id,
                idle_start_time=datetime(2026, 7, 27, 7, 15),
                idle_end_time=datetime(2026, 7, 27, 7, 30),
                reason='In Meeting'
            )
        ])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_daily_report_uses_session_sum_and_all_nonproductive_time(self):
        report = WebService.get_daily_time_report_list(
            self.user_id,
            date(2026, 7, 27),
            date(2026, 7, 27)
        )

        self.assertEqual(len(report), 1)
        row = report[0]
        self.assertAlmostEqual(row['gross_session_hours'], 3.0)
        self.assertAlmostEqual(row['break_hours'], 20 / 60)
        self.assertAlmostEqual(row['net_hours_worked'], 2 + 40 / 60)
        self.assertAlmostEqual(
            row['gross_session_hours'],
            row['net_hours_worked'] + row['break_hours']
        )

    def test_range_summary_matches_daily_report(self):
        summary = WebService.get_daily_summary(
            self.user_id,
            date(2026, 7, 27),
            date(2026, 7, 27)
        )

        self.assertAlmostEqual(summary['gross_worked_hours'], 3.0)
        self.assertAlmostEqual(summary['total_break_time_hours'], 20 / 60)
        self.assertAlmostEqual(summary['productive_worked_hours'], 2 + 40 / 60)


if __name__ == '__main__':
    unittest.main()
