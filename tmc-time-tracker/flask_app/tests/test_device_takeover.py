import os
import unittest
from datetime import datetime, timedelta

import pytz

os.environ['SECRET_KEY'] = 'device-takeover-test'
os.environ['DATABASE_URL'] = 'sqlite://'

from app import create_app, db
from app.models import User, UserDeviceSession


class DeviceTakeoverTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    @staticmethod
    def login_payload(device_id, device_name, confirm=False):
        return {
            'microsoft_oid': 'oid-alex',
            'email': 'alex@tm-connect.de',
            'username': 'Alex Example',
            'mac_address': device_id,
            'device_name': device_name,
            'confirm_takeover': confirm
        }

    def test_active_pc_requires_confirmation_and_is_then_replaced(self):
        first_login = self.client.post(
            '/api/electron_sso_login',
            json=self.login_payload('device-a', 'PC-1')
        )
        self.assertEqual(first_login.status_code, 200)

        warning = self.client.post(
            '/api/electron_sso_login',
            json=self.login_payload('device-b', 'PC-2')
        )
        self.assertEqual(warning.status_code, 409)
        self.assertEqual(warning.get_json()['code'], 'E_ACTIVE_SESSION')
        self.assertEqual(warning.get_json()['previous_device_name'], 'PC-1')

        takeover = self.client.post(
            '/api/electron_sso_login',
            json=self.login_payload('device-b', 'PC-2', confirm=True)
        )
        self.assertEqual(takeover.status_code, 200)
        self.assertTrue(takeover.get_json()['session_taken_over'])

        old_heartbeat = self.client.post(
            '/api/heartbeat_ping/oid-alex',
            json={'device_id': 'device-a'}
        )
        self.assertEqual(old_heartbeat.status_code, 409)
        self.assertEqual(old_heartbeat.get_json()['code'], 'E_SESSION_REPLACED')

        new_heartbeat = self.client.post(
            '/api/heartbeat_ping/oid-alex',
            json={'device_id': 'device-b'}
        )
        self.assertEqual(new_heartbeat.status_code, 200)

    def test_stale_pc_can_be_replaced_without_warning(self):
        self.client.post(
            '/api/electron_sso_login',
            json=self.login_payload('device-a', 'PC-1')
        )
        user = User.query.filter_by(microsoft_oid='oid-alex').one()
        session = UserDeviceSession.query.filter_by(
            user_id=user.id,
            device_id='device-a'
        ).one()
        session.last_heartbeat = (
            datetime.now(pytz.utc).replace(tzinfo=None) - timedelta(minutes=5)
        )
        db.session.commit()

        replacement = self.client.post(
            '/api/electron_sso_login',
            json=self.login_payload('device-b', 'PC-2')
        )

        self.assertEqual(replacement.status_code, 200)
        self.assertFalse(replacement.get_json()['session_taken_over'])

    def test_logout_releases_device_but_keeps_account(self):
        self.client.post(
            '/api/electron_sso_login',
            json=self.login_payload('device-a', 'PC-1')
        )

        response = self.client.post(
            '/api/electron_logout',
            json={
                'microsoft_oid': 'oid-alex',
                'device_id': 'device-a'
            }
        )

        self.assertEqual(response.status_code, 200)
        user = User.query.filter_by(microsoft_oid='oid-alex').one()
        self.assertIsNone(user.device_mac_address)

    def test_shared_pc_can_switch_to_another_account(self):
        self.client.post(
            '/api/electron_sso_login',
            json=self.login_payload('device-a', 'PC-1')
        )
        second_payload = {
            'microsoft_oid': 'oid-maria',
            'email': 'maria@tm-connect.de',
            'username': 'Maria Example',
            'mac_address': 'device-a',
            'device_name': 'PC-1'
        }

        response = self.client.post(
            '/api/electron_sso_login',
            json=second_payload
        )

        self.assertEqual(response.status_code, 200)
        alex = User.query.filter_by(microsoft_oid='oid-alex').one()
        maria = User.query.filter_by(microsoft_oid='oid-maria').one()
        self.assertIsNone(alex.device_mac_address)
        self.assertEqual(maria.device_mac_address, 'device-a')


if __name__ == '__main__':
    unittest.main()
