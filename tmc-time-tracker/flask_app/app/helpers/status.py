import json
from datetime import datetime, timedelta
from ..models import db, User, TimeEntry, BreakEntry, IdleEntry, ActivityLog, UserDeviceSession
from .calculations import calculate_worked_hours

def get_programmatic_status(user_id, is_admin_view=True):
    user = User.query.get(user_id)
    if not user:
        return 'Inactive'

    active_entry = TimeEntry.query.filter_by(user_id=user_id, clock_out_time=None).first()
    if not active_entry:
        return 'Inactive'

    active_break = BreakEntry.query.filter_by(time_entry_id=active_entry.id, break_end_time=None).first()
    if active_break:
        current_status = 'On Break'
    elif IdleEntry.query.filter_by(time_entry_id=active_entry.id, idle_end_time=None).first():
        current_status = 'Idle'
    else:
        current_status = 'Active'

    if not is_admin_view:
        return 'Active' if current_status != 'Inactive' else 'Inactive'

    return current_status

def finalize_idle_period(idle_entry, end_time_utc, reason=None, commit=True):
    if idle_entry.idle_end_time is None:
        idle_entry.idle_end_time = end_time_utc.replace(tzinfo=None)
    if reason:
        idle_entry.reason = reason
    if commit:
        db.session.commit()

def force_clock_out(entry, reason, clock_out_time=None):
    if not clock_out_time:
        clock_out_time = datetime.utcnow()

    # Finalize breaks
    for b in entry.break_entries:
        if b.break_end_time is None:
            b.break_end_time = clock_out_time

    # Finalize idle periods
    for i in entry.idle_entries:
        if i.idle_end_time is None:
            finalize_idle_period(i, clock_out_time, reason=reason)

    # Update entry
    entry.clock_out_time = clock_out_time.replace(tzinfo=None)
    entry.status = reason
    entry.total_worked_hours = calculate_worked_hours(entry)

    # Log the event
    db.session.add(ActivityLog(
        user_id=entry.user_id,
        time_entry_id=entry.id,
        timestamp=clock_out_time,
        event_type='clock_out',
        details=json.dumps({'source': reason})
    ))

def is_user_globally_active(user_id, triggering_device_id, tolerance_minutes=2):
    """
    Checks if the user has any OTHER active devices that are not idle/on break.
    Tolerance is kept short (2 mins) to align with heartbeat frequency.
    """
    if not triggering_device_id:
        return False
        
    cutoff_time = datetime.utcnow() - timedelta(minutes=tolerance_minutes)
    
    # Query the dedicated device session table with indexed columns
    other_active_devices = UserDeviceSession.query.filter(
        UserDeviceSession.user_id == user_id,
        UserDeviceSession.device_id != triggering_device_id,
        UserDeviceSession.last_heartbeat >= cutoff_time,
        UserDeviceSession.is_idle == False
    ).first()
    
    return other_active_devices is not None