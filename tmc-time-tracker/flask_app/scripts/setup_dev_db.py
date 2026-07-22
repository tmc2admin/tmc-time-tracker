import os
import sys
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))
load_dotenv(BASE_DIR / ".env")

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("ENABLE_DEV_LOGIN", "1")
os.environ.setdefault("SECRET_KEY", "local-dev-secret-change-me")
os.environ.setdefault("DATABASE_URL", "sqlite:///dev_time_tracker.db")

from app import create_app, db
from app.models import CompanyConfig, User


def main():
    app = create_app()
    with app.app_context():
        db.create_all()

        config = CompanyConfig.query.first()
        if not config:
            config = CompanyConfig(
                company_name="TMC Time Tracker Local",
                default_daily_hours=8.0,
                default_working_days="Monday,Tuesday,Wednesday,Thursday,Friday",
            )
            db.session.add(config)
            db.session.flush()

        email = os.getenv("DEV_LOGIN_EMAIL", "dev.admin@tm-connect.de")
        user = User.query.filter_by(email=email).first()
        if not user:
            user = User(
                username=os.getenv("DEV_LOGIN_NAME", "Local Test Admin"),
                email=email,
                microsoft_oid=os.getenv("DEV_LOGIN_OID", "local-dev-admin"),
                is_admin=os.getenv("DEV_LOGIN_IS_ADMIN", "1") == "1",
                default_daily_hours=config.default_daily_hours or 8.0,
                default_working_days=config.default_working_days or "Monday,Tuesday,Wednesday,Thursday,Friday",
            )
            db.session.add(user)

        db.session.commit()
        print("Local dev database is ready.")
        print(f"Web login: http://localhost:5000/dev-login")


if __name__ == "__main__":
    main()
