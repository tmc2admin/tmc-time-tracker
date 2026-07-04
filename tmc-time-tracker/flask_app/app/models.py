# flask_app/app/models.py

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app import db
from datetime import datetime, time, timedelta
from sqlalchemy import text
from sqlalchemy.types import Date, Numeric


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256))
    is_admin = db.Column(db.Boolean, default=False)
    microsoft_oid = db.Column(db.String(100), unique=True, nullable=True)
    device_mac_address = db.Column(db.String(48), unique=True, nullable=True)
    default_daily_hours = db.Column(db.Float, default=8.0)
    default_working_days = db.Column(db.String(255), default='Monday,Tuesday,Wednesday,Thursday,Friday')
    time_entries = db.relationship('TimeEntry', backref='user', lazy=True)
    session_start_time = db.Column(db.Time, nullable=False, server_default=text("'09:00:00'"))
    session_end_time = db.Column(db.Time, nullable=False, server_default=text("'18:00:00'"))
    overtime_end_time = db.Column(db.DateTime, nullable=True) 
    is_suspended = db.Column(db.Boolean, default=False, nullable=False) 
    provision_percentage = db.Column(db.Float, nullable=True, default=100.0)
    last_heartbeat_utc = db.Column(db.DateTime, nullable=True, server_default=db.func.utcnow())
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class TimeEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    clock_in_time = db.Column(db.DateTime, nullable=False)
    clock_out_time = db.Column(db.DateTime)
    status = db.Column(db.String(50), default='active')
    total_worked_hours = db.Column(db.Float, default=0.0)
    break_entries = db.relationship('BreakEntry', backref='time_entry', lazy=True, cascade="all, delete-orphan")
    idle_entries = db.relationship('IdleEntry', backref='time_entry', lazy=True, cascade="all, delete-orphan")
    notes = db.Column(db.String(255), nullable=True)
    client_version = db.Column(db.String(50), nullable=True) 

class BreakEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    time_entry_id = db.Column(db.Integer, db.ForeignKey('time_entry.id'), nullable=False)
    break_start_time = db.Column(db.DateTime, nullable=False)
    break_end_time = db.Column(db.DateTime)
    reason = db.Column(db.String(255), nullable=True)

class IdleEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    time_entry_id = db.Column(db.Integer, db.ForeignKey('time_entry.id'), nullable=False)
    idle_start_time = db.Column(db.DateTime, nullable=False)
    idle_end_time = db.Column(db.DateTime)
    reason = db.Column(db.String(255), nullable=True)

class CompanyConfig(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_name = db.Column(db.String(100), default='TMC Time Tracker')
    default_daily_hours = db.Column(db.Float, default=8.0)
    default_working_days = db.Column(db.String(255), default='Monday,Tuesday,Wednesday,Thursday,Friday')
    working_hours_start = db.Column(db.Time, nullable=False, server_default=text("'07:00:00'"))
    working_hours_end = db.Column(db.Time, nullable=False, server_default=text("'18:00:00'"))
    max_idle_minutes = db.Column(db.Integer, nullable=False, server_default=text("5"))
    idle_to_break_minutes = db.Column(db.Integer, nullable=False, server_default=text("10"))
    long_break_prompt_minutes = db.Column(db.Integer, nullable=False, server_default=text("90"))
    auto_clock_out_after_break_minutes = db.Column(db.Integer, nullable=False, server_default=text("120"))
 

class UserLocationLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    time_entry_id = db.Column(db.Integer, db.ForeignKey('time_entry.id'), nullable=False)
    timestamp = db.Column(db.DateTime, default=db.func.current_timestamp())
    ip_address = db.Column(db.String(45))
    country = db.Column(db.String(100))
    city = db.Column(db.String(100))
    region = db.Column(db.String(100))

class ActivityLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    time_entry_id = db.Column(db.Integer, db.ForeignKey('time_entry.id'), nullable=True)
    timestamp = db.Column(db.DateTime, nullable=False)
    event_type = db.Column(db.String(50), nullable=False)
    details = db.Column(db.Text, nullable=True)

class ApplicationUsage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    time_entry_id = db.Column(db.Integer, db.ForeignKey('time_entry.id'), nullable=True)
    application_name = db.Column(db.String(255), nullable=False)
    window_title = db.Column(db.String(512))
    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime)
    duration_seconds = db.Column(db.Integer)
    device_id = db.Column(db.String(100), nullable=True)

class AutomatedStateChange(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    time_entry_id = db.Column(db.Integer, db.ForeignKey('time_entry.id'), nullable=True)
    from_state = db.Column(db.String(50))
    to_state = db.Column(db.String(50))
    timestamp = db.Column(db.DateTime, nullable=False)
    trigger_reason = db.Column(db.String(255))

class OvertimeAllocation(db.Model):
    __tablename__ = 'overtime_allocation'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    reason = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(20), default='Pending')  # Options: Pending, Approved, Rejected
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref=db.backref('overtime_requests', lazy=True))

    @property
    def duration_hours(self):
        """Calculates duration in hours."""
        dummy_date = datetime(2000, 1, 1)
        start = datetime.combine(dummy_date, self.start_time)
        end = datetime.combine(dummy_date, self.end_time)
        if end < start:
            end += timedelta(days=1)
        diff = end - start
        return diff.total_seconds() / 3600.0

class Holiday(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    date = db.Column(db.Date, unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())

    def __repr__(self):
        return f'<Holiday {self.name} on {self.date}>'
    
class Provision(db.Model):
    __tablename__ = 'provision'
    
    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    beschreibung = db.Column(db.String(255), nullable=False)
    zielvereinbarung = db.Column(db.Integer, nullable=False)
    start_date = db.Column(Date, nullable=False)
    end_date = db.Column(Date, nullable=False)
    grenze_punkte = db.Column(db.Integer, nullable=False)
    typ = db.Column(db.String(100), nullable=False)
    provision_percent = db.Column(Numeric(10, 2), nullable=True)
    provision_euro = db.Column(Numeric(10, 2), nullable=True)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())

    user = db.relationship('User', backref=db.backref('provisions', lazy=True))
    
    def __repr__(self):
        return f'<Provision {self.beschreibung}>'
    
class LeaveRequest(db.Model):
    __tablename__ = 'leave_request'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    reason = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(20), default='Pending') 
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationship to User
    user = db.relationship('User', backref=db.backref('leave_requests', lazy=True))

    # --- UPDATED METHOD ---
    @property
    def duration_days(self):
        """Calculates business days (Mon-Fri) only."""
        from datetime import timedelta
        
        day_count = 0
        curr = self.start_date
        while curr <= self.end_date:
            # 0=Monday ... 4=Friday, 5=Saturday, 6=Sunday
            if curr.weekday() < 5: 
                day_count += 1
            curr += timedelta(days=1)
        return day_count
    
class TelekomPassword(db.Model):
    __tablename__ = 'telekom_password'
    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.String(100), nullable=False)
    password_value = db.Column(db.String(255), nullable=False) # Stored encrypted ideally, or plain for this MVP
    expiration_date = db.Column(db.Date, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)

    @property
    def is_expired(self):
        return self.expiration_date < datetime.now().date()

class Customer(db.Model):
    __tablename__ = 'customer'
    id = db.Column(db.Integer, primary_key=True)
    company_name = db.Column(db.String(100), nullable=False)
    service_status = db.Column(db.String(20), default='Active') # Active, Inactive
    research_notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class UserDeviceSession(db.Model):
    """
    Tracks state per device. 
    User is 'Global Active' if ANY device_session is active.
    """
    __tablename__ = 'user_device_session'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    # Store MAC or unique Machine ID here
    device_id = db.Column(db.String(100), nullable=False) 
    
    # Device specific state
    is_idle = db.Column(db.Boolean, default=False)
    last_heartbeat = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Metadata for debugging
    device_name = db.Column(db.String(100), nullable=True) 
    ip_address = db.Column(db.String(50), nullable=True)

    __table_args__ = (
        # Ensure one active session entry per device per user
        db.UniqueConstraint('user_id', 'device_id', name='_user_device_uc'),
    )

class CoverageEdit(db.Model):
    """
    Local table to store user edits for external KLSID data
    """
    __tablename__ = 'coverage_edits'
    
    id = db.Column(db.Integer, primary_key=True)
    klsid = db.Column(db.String(50), unique=True, nullable=False, index=True)
    still_in_stock = db.Column(db.String(3), nullable=True)
    new_customer = db.Column(db.Text, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_updated = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = db.Column(db.String(100), nullable=True)
    created_by = db.Column(db.String(100), nullable=True)
    
    def __repr__(self):
        return f'<CoverageEdit {self.klsid}>'