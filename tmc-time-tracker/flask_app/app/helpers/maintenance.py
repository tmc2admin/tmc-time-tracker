import json
from datetime import datetime, timedelta, time
import pytz
from flask import current_app
from sqlalchemy import cast, Date, func
from ..models import db, User, TimeEntry, ActivityLog, UserDeviceSession
from .formatters import BERLIN_TZ
from .calculations import calculate_worked_hours
from .status import force_clock_out

def _handle_stale_sessions_for_user(user_id):
    user = User.query.get(user_id)
    if not user:
        return

    now_berlin = datetime.now(BERLIN_TZ)
    today_berlin = now_berlin.date()

    stale_entries = TimeEntry.query.filter(
        TimeEntry.user_id == user_id,
        TimeEntry.clock_out_time.is_(None),
        cast(TimeEntry.clock_in_time, Date) < today_berlin
    ).all()

    for entry in stale_entries:
        current_app.logger.info(f"Found and closed stale session {entry.id} for user {user.username}.")
        
        entry_start_date_berlin = pytz.utc.localize(entry.clock_in_time).astimezone(BERLIN_TZ).date()
        end_of_day_berlin = BERLIN_TZ.localize(datetime.combine(entry_start_date_berlin, time(23, 59, 59)))
        end_of_day_utc = end_of_day_berlin.astimezone(pytz.utc)

        entry.clock_out_time = end_of_day_utc.replace(tzinfo=None)
        entry.status = 'stale_closed'
        entry.total_worked_hours = calculate_worked_hours(entry)
        
        db.session.add(ActivityLog(
            user_id=user.id, time_entry_id=entry.id, timestamp=entry.clock_out_time,
            event_type='auto_close_stale', details=json.dumps({'reason': 'Session left open from a previous day.'})
        ))

    if stale_entries:
        db.session.commit()

def find_and_clock_out_zombie_sessions():
    """
    Clocks out users who have NO active devices reporting in.
    Refactored to support Multi-Device (UserDeviceSession).
    """
    current_app.logger.info("[Scheduler] Running ZOMBIE session cleanup...")
    
    HEARTBEAT_TOLERANCE_MINUTES = 5
    now_utc = datetime.utcnow()
    tolerance_time = now_utc - timedelta(minutes=HEARTBEAT_TOLERANCE_MINUTES)
    
    # 1. Get all users currently clocked in
    # Use joinedload to prevent N+1 queries when accessing entry.user later
    active_entries = TimeEntry.query.options(
        db.joinedload(TimeEntry.user)
    ).filter(
        TimeEntry.clock_out_time.is_(None)
    ).all()

    if not active_entries:
        current_app.logger.info("[Scheduler] No active sessions found to check.")
        return 0

    cleaned_count = 0

    for entry in active_entries:
        user = entry.user
        
        # 2. Find the LATEST heartbeat across ALL devices for this user
        # We query the new UserDeviceSession table for the most recent timestamp
        last_device_heartbeat = db.session.query(
            func.max(UserDeviceSession.last_heartbeat)
        ).filter_by(user_id=user.id).scalar()

        # 3. Aggregation Strategy (The "OR" Logic)
        # We compare the User model's legacy heartbeat AND the Device table.
        # This handles cases where the new Device table might not be populated yet,
        # or if a legacy client is still hitting the old endpoint.
        timestamps = []
        if last_device_heartbeat:
            timestamps.append(last_device_heartbeat)
        if user.last_heartbeat_utc:
            timestamps.append(user.last_heartbeat_utc)
            
        # If we have absolutely no data, assume they are dead (or just clocked in)
        # We fallback to clock_in_time to prevent immediate logout on instant crash
        last_seen = max(timestamps) if timestamps else entry.clock_in_time

        # 4. Check Tolerance
        if last_seen and last_seen < tolerance_time:
            try:
                current_app.logger.warn(
                    f"[Scheduler] Zombie detected: {user.email}. "
                    f"Last seen: {last_seen} (Tolerance: {tolerance_time})"
                )

                # Determine a logical clock-out time
                # If they died at 12:00, clock them out at 12:00, not 12:05
                forced_out_time = last_seen

                force_clock_out(
                    entry=entry, 
                    reason='heartbeat_timeout',
                    clock_out_time=forced_out_time
                )
                
                cleaned_count += 1
                
                # OPTIONAL: Clean up the stale device session rows for this user
                # so the table doesn't grow infinitely
                db.session.query(UserDeviceSession).filter(
                    UserDeviceSession.user_id == user.id,
                    UserDeviceSession.last_heartbeat < tolerance_time
                ).delete(synchronize_session=False)

            except Exception as e:
                db.session.rollback()
                current_app.logger.error(f"Failed to clock out zombie entry {entry.id}: {str(e)}")

    if cleaned_count > 0:
        db.session.commit()
        
    current_app.logger.info(f"[Scheduler] Zombie session cleanup finished. Cleaned {cleaned_count} sessions.")
    return cleaned_count

def send_clock_out_email(user, time_entry):
    pass