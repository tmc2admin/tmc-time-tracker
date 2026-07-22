"""Set default clock-out time to 22:00.

Revision ID: 7c8e9a1b2d3f
Revises: 2fe9b22435ea
Create Date: 2026-07-08 19:20:00.000000

"""
from alembic import op


revision = '7c8e9a1b2d3f'
down_revision = '2fe9b22435ea'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "UPDATE company_config "
        "SET working_hours_end = '22:00:00' "
        "WHERE working_hours_end = '18:00:00'"
    )
    op.execute(
        "UPDATE [user] "
        "SET session_end_time = '22:00:00' "
        "WHERE session_end_time = '18:00:00'"
    )


def downgrade():
    op.execute(
        "UPDATE company_config "
        "SET working_hours_end = '18:00:00' "
        "WHERE working_hours_end = '22:00:00'"
    )
    op.execute(
        "UPDATE [user] "
        "SET session_end_time = '18:00:00' "
        "WHERE session_end_time = '22:00:00'"
    )
