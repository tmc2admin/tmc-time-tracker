"""Add Hausverwaltung mail follow-up tracking (watched domains, follow-ups, user mail token).

Revision ID: 9a1f4c7b3e02
Revises: 7c8e9a1b2d3f
Create Date: 2026-10-30 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = '9a1f4c7b3e02'
down_revision = '7c8e9a1b2d3f'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('user', sa.Column('ms_mail_refresh_token_enc', sa.Text(), nullable=True))
    op.add_column('user', sa.Column('ms_mail_token_scope', sa.String(length=255), nullable=True))
    op.add_column('user', sa.Column('ms_mail_token_updated_at', sa.DateTime(), nullable=True))

    op.create_table(
        'mail_watched_domain',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('domain', sa.String(length=255), nullable=False),
        sa.Column('label', sa.String(length=150), nullable=True),
        sa.Column('is_keyword', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.UniqueConstraint('domain', name='uq_mail_watched_domain_domain'),
    )

    op.create_table(
        'mail_follow_up',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
        sa.Column('conversation_id', sa.String(length=255), nullable=False),
        sa.Column('source_message_id', sa.String(length=255), nullable=True),
        sa.Column('web_link', sa.String(length=1024), nullable=True),
        sa.Column('subject', sa.String(length=500), nullable=True),
        sa.Column('counterpart_name', sa.String(length=255), nullable=True),
        sa.Column('counterpart_email', sa.String(length=255), nullable=True),
        sa.Column('company_label', sa.String(length=255), nullable=True),
        sa.Column('last_inbound_at', sa.DateTime(), nullable=True),
        sa.Column('last_outbound_at', sa.DateTime(), nullable=True),
        sa.Column('last_inbound_preview', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='open'),
        sa.Column('due_date', sa.Date(), nullable=True),
        sa.Column('draft_text', sa.Text(), nullable=True),
        sa.Column('draft_generated_at', sa.DateTime(), nullable=True),
        sa.Column('draft_error', sa.String(length=500), nullable=True),
        sa.Column('outlook_draft_id', sa.String(length=255), nullable=True),
        sa.Column('outlook_draft_web_link', sa.String(length=1024), nullable=True),
        sa.Column('outlook_draft_created_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.UniqueConstraint('user_id', 'conversation_id', name='_user_conversation_uc'),
    )
    op.create_index(
        'ix_mail_follow_up_conversation_id', 'mail_follow_up', ['conversation_id']
    )

    # Seed with the Hausverwaltung domains already seen in real correspondence, so the
    # feature has something to match on from day one. Admins can add/remove more later
    # under /admin/mail_watched_domains.
    mail_watched_domain = sa.table(
        'mail_watched_domain',
        sa.column('domain', sa.String),
        sa.column('label', sa.String),
        sa.column('is_keyword', sa.Boolean),
    )
    op.bulk_insert(
        mail_watched_domain,
        [
            {'domain': 'hv-philipp.de', 'label': 'B. Philipp Hausverwaltungs GmbH', 'is_keyword': False},
            {'domain': 'format-hv.de', 'label': 'format Hausverwaltungen GmbH', 'is_keyword': False},
            {'domain': 'schoenundsever.de', 'label': 'Schön & Sever Hausverwaltungs GmbH', 'is_keyword': False},
            {'domain': 'grk.de', 'label': 'GRK-Gruppe', 'is_keyword': False},
            {'domain': 'dringend', 'label': None, 'is_keyword': True},
            {'domain': 'wiedervorlage', 'label': None, 'is_keyword': True},
            {'domain': 'hausverwaltung', 'label': None, 'is_keyword': True},
        ],
    )


def downgrade():
    op.drop_index('ix_mail_follow_up_conversation_id', table_name='mail_follow_up')
    op.drop_table('mail_follow_up')
    op.drop_table('mail_watched_domain')
    op.drop_column('user', 'ms_mail_token_updated_at')
    op.drop_column('user', 'ms_mail_token_scope')
    op.drop_column('user', 'ms_mail_refresh_token_enc')
