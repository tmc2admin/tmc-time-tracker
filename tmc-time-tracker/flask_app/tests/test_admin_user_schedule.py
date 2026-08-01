import os
import unittest
from datetime import time

os.environ['SECRET_KEY'] = 'admin-schedule-test'
os.environ['DATABASE_URL'] = 'sqlite://'

from app import create_app, db
from app.models import User


class AdminUserScheduleTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()

        self.admin = User(
            username='Admin',
            email='admin@tm-connect.de',
            is_admin=True
        )
        self.employee = User(
            username='Employee',
            email='employee@tm-connect.de'
        )
        db.session.add_all([self.admin, self.employee])
        db.session.commit()

        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session['_user_id'] = str(self.admin.id)
            session['_fresh'] = True

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_admin_can_update_employee_schedule(self):
        response = self.client.post(
            f'/admin/users/{self.employee.id}',
            data={
                f'user-{self.employee.id}-default_daily_hours': '7.5',
                f'user-{self.employee.id}-default_working_days': [
                    'Monday', 'Tuesday', 'Wednesday', 'Thursday'
                ],
                f'user-{self.employee.id}-session_start_time': '07:30',
                f'user-{self.employee.id}-session_end_time': '16:15',
            },
            follow_redirects=True
        )

        db.session.refresh(self.employee)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.employee.default_daily_hours, 7.5)
        self.assertEqual(
            self.employee.default_working_days,
            'Monday,Tuesday,Wednesday,Thursday'
        )
        self.assertEqual(self.employee.session_start_time, time(7, 30))
        self.assertEqual(self.employee.session_end_time, time(16, 15))


if __name__ == '__main__':
    unittest.main()
