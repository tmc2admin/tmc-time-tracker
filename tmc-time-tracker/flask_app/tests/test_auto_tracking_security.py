import os
import unittest
from datetime import datetime, time, timedelta

import pytz

os.environ['SECRET_KEY'] = 'auto-tracking-test'
os.environ['DATABASE_URL'] = 'sqlite://'

from app import create_app, db
from app.models import (
    ActivityLog, BreakEntry, CompanyConfig, TimeEntry, User
)


BERLIN_TZ = pytz.timezone('Europe/Berlin')


class AutoTrackingSecurityTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()

        db.session.add(CompanyConfig(
            working_hours_start=time(0, 0),
            working_hours_end=time(23, 59),
            max_idle_minutes=5,
            idle_to_break_minutes=10,
            long_break_prompt_minutes=90,
            auto_clock_out_after_break_minutes=120,
        ))
        db.session.commit()

        self.client = self.app.test_client()
        today_name = datetime.now(BERLIN_TZ).strftime('%A')
        login_response = self.client.post('/api/electron_sso_login', json={
            'microsoft_oid': 'oid-auto',
            'email': 'auto@tm-connect.de',
            'username': 'Auto Employee',
            'mac_address': 'device-auto',
            'device_name': 'AUTO-PC'
        })
        self.assertEqual(login_response.status_code, 200)

        self.user = User.query.filter_by(microsoft_oid='oid-auto').one()
        self.user.default_working_days = today_name
        self.user.session_start_time = time(0, 0)
        self.user.session_end_time = time(23, 59)
        db.session.commit()

        self.headers = {'X-Device-ID': 'device-auto'}

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def automatic_clock_in(self, proof=None):
        return self.client.post(
            '/api/clock_in_for_electron/oid-auto',
            headers=self.headers,
            json={
                'automatic': True,
                'client_version': '2.7.6',
                'timestamp': datetime.now(pytz.utc).isoformat(),
                'activity_proof': proof or {}
            }
        )

    def test_config_uses_employee_schedule(self):
        self.user.session_start_time = time(6, 15)
        self.user.session_end_time = time(19, 45)
        self.user.default_working_days = 'Monday,Wednesday'
        db.session.commit()

        response = self.client.get(f'/api/v1/config/{self.user.id}')
        data = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data['startTime'], '06:15:00')
        self.assertEqual(data['endTime'], '19:45:00')
        self.assertEqual(data['workingDays'], ['Monday', 'Wednesday'])
        self.assertTrue(data['autoClockInEnabled'])

    def test_automatic_clock_in_requires_meaningful_recent_input(self):
        missing_proof = self.automatic_clock_in()
        self.assertEqual(missing_proof.status_code, 403)
        self.assertEqual(missing_proof.get_json()['code'], 'E_ACTIVITY_REQUIRED')

        mouse_move_only = self.automatic_clock_in({
            'event_type': 'mousemove',
            'system_idle_seconds': 0
        })
        self.assertEqual(mouse_move_only.status_code, 403)

        accepted = self.automatic_clock_in({
            'event_type': 'keydown',
            'system_idle_seconds': 0
        })
        self.assertEqual(accepted.status_code, 200)
        self.assertIsNotNone(TimeEntry.query.filter_by(
            user_id=self.user.id,
            clock_out_time=None
        ).first())

    def test_clock_out_uses_server_time_instead_of_client_time(self):
        self.assertEqual(self.automatic_clock_in({
            'event_type': 'mousedown',
            'system_idle_seconds': 0
        }).status_code, 200)

        response = self.client.post(
            '/api/clock_out_for_electron/oid-auto',
            headers=self.headers,
            json={'source': 'manual', 'timestamp': '2024-01-01T00:00:00Z'}
        )
        entry = TimeEntry.query.filter_by(user_id=self.user.id).one()

        self.assertEqual(response.status_code, 200)
        self.assertGreater(entry.clock_out_time, datetime.utcnow() - timedelta(minutes=1))

    def test_technical_heartbeat_is_not_treated_as_user_input(self):
        self.assertEqual(self.automatic_clock_in({
            'event_type': 'keydown',
            'system_idle_seconds': 0
        }).status_code, 200)
        entry = TimeEntry.query.filter_by(user_id=self.user.id).one()
        old_time = datetime.utcnow() - timedelta(minutes=20)
        entry.clock_in_time = old_time
        ActivityLog.query.filter_by(
            user_id=self.user.id,
            event_type='clock_in'
        ).update({'timestamp': old_time})
        db.session.commit()

        heartbeat = self.client.post(
            '/api/heartbeat_ping/oid-auto',
            headers=self.headers,
            json={'activity_evidence': {'system_idle_seconds': 0}}
        )
        self.assertEqual(heartbeat.status_code, 200)
        self.assertIsNone(ActivityLog.query.filter_by(
            user_id=self.user.id,
            event_type='input_heartbeat'
        ).first())

        cleanup = self.client.post('/api/heartbeat')
        self.assertEqual(cleanup.status_code, 200)
        self.assertIsNotNone(BreakEntry.query.filter_by(
            time_entry_id=entry.id,
            break_end_time=None
        ).first())

    def test_meaningful_heartbeat_records_aggregate_without_key_content(self):
        self.assertEqual(self.automatic_clock_in({
            'event_type': 'keydown',
            'system_idle_seconds': 0
        }).status_code, 200)

        response = self.client.post(
            '/api/heartbeat_ping/oid-auto',
            headers=self.headers,
            json={'activity_evidence': {
                'keydown': 3,
                'mousedown': 1,
                'mousemove': 12,
                'mouse_distance': 800,
                'system_idle_seconds': 1,
                'interval_seconds': 15
            }}
        )
        activity = ActivityLog.query.filter_by(
            user_id=self.user.id,
            event_type='input_heartbeat'
        ).one()

        self.assertEqual(response.status_code, 200)
        self.assertIn('"keydown": 3', activity.details)
        self.assertNotIn('key_content', activity.details)


if __name__ == '__main__':
    unittest.main()
