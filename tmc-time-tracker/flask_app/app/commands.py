# flask_app/app/commands.py
import click
from flask.cli import with_appcontext
from . import db
from .models import CompanyConfig, User
from datetime import time

# This creates a new command that you can run with "flask seed"
@click.command('seed')
@with_appcontext
def seed_command():
    """Populates the database with initial, essential data."""

    # --- 1. Seed Company Configuration ---
    # Check if a configuration already exists to prevent duplicates
    if CompanyConfig.query.first():
        click.echo('CompanyConfig already exists. Skipping.')
    else:
        click.echo('Creating default CompanyConfig...')
        default_config = CompanyConfig(
            default_daily_hours=8.0,
            default_working_days='Monday,Tuesday,Wednesday,Thursday,Friday',
            working_hours_start=time(9, 0),
            working_hours_end=time(22, 0),
            max_idle_minutes=5,
            idle_to_break_minutes=15,
            long_break_prompt_minutes=60
        )
        db.session.add(default_config)
        click.echo('Default CompanyConfig created.')

    # --- 2. Seed an Admin User ---
    # Check if an admin user already exists
    if User.query.filter_by(is_admin=True).first():
        click.echo('An admin user already exists. Skipping.')
    else:
        click.echo('Creating default admin user...')
        # IMPORTANT: Use environment variables or a secure method for production passwords.
        admin_user = User(
            username='Torsten Müller',
            email='torsten.mueller@tm-connect.de',
            microsoft_oid='34fsdf-4aab-a179-ca13ffbace38', 
            is_admin=True
        )
        # You might want to add a default password if your User model supports it
        # admin_user.set_password('your-secure-password') 
        db.session.add(admin_user)
        click.echo('Default admin user created.')

    # --- 3. Commit the changes to the database ---
    db.session.commit()
    click.echo('Database has been successfully seeded.')
