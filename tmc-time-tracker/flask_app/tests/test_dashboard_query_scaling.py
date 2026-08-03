import os
import unittest
from datetime import datetime, timedelta

from sqlalchemy import event

os.environ['SECRET_KEY'] = 'dashboard-query-scaling-test'
os.environ['DATABASE_URL'] = 'sqlite://'

from app import create_app, db
from app.models import BreakEntry, IdleEntry, TimeEntry, User


class DashboardQueryScalingTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.client = self.app.test_client()

        self.user = User(
            username='Scaling User',
            email='scaling@tm-connect.de',
            microsoft_oid='scaling-oid',
            default_daily_hours=8.0
        )
        db.session.add(self.user)
        db.session.flush()

        now = datetime.utcnow()
        entry = TimeEntry(
            user_id=self.user.id,
            clock_in_time=now - timedelta(hours=2),
            clock_out_time=now - timedelta(hours=1),
            status='completed',
            client_version='2.6.0'
        )
        db.session.add(entry)
        db.session.flush()

        for index in range(40):
            started = entry.clock_in_time + timedelta(seconds=index * 30)
            db.session.add(BreakEntry(
                time_entry_id=entry.id,
                break_start_time=started,
                break_end_time=started + timedelta(seconds=5)
            ))

        for index in range(30):
            started = entry.clock_in_time + timedelta(seconds=index * 40)
            db.session.add(IdleEntry(
                time_entry_id=entry.id,
                idle_start_time=started,
                idle_end_time=started + timedelta(seconds=10)
            ))

        db.session.commit()
        self.statements = []

        def capture_statement(conn, cursor, statement, parameters, context, executemany):
            self.statements.append(statement.lower())

        self.capture_statement = capture_statement
        event.listen(db.engine, 'before_cursor_execute', self.capture_statement)

    def tearDown(self):
        event.remove(db.engine, 'before_cursor_execute', self.capture_statement)
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_dashboard_does_not_join_breaks_and_idles_together(self):
        response = self.client.get(
            f'/api/dashboard_data_for_electron/{self.user.id}'
        )

        self.assertEqual(response.status_code, 200)
        combined_collection_joins = [
            statement for statement in self.statements
            if 'join break_entry' in statement and 'join idle_entry' in statement
        ]
        self.assertEqual(combined_collection_joins, [])
        self.assertTrue(any('from break_entry' in statement for statement in self.statements))
        self.assertTrue(any('from idle_entry' in statement for statement in self.statements))

    def test_clock_in_requires_a_supported_desktop_version(self):
        outdated = self.client.post(
            '/api/clock_in_for_electron/scaling-oid',
            json={'client_version': '2.6.0'}
        )
        missing = self.client.post(
            '/api/clock_in_for_electron/scaling-oid',
            json={}
        )
        supported = self.client.post(
            '/api/clock_in_for_electron/scaling-oid',
            json={'client_version': '2.7.4'}
        )

        self.assertEqual(outdated.status_code, 426)
        self.assertEqual(outdated.get_json()['code'], 'E_CLIENT_UPDATE_REQUIRED')
        self.assertEqual(missing.status_code, 426)
        self.assertEqual(supported.status_code, 200)


if __name__ == '__main__':
    unittest.main()
