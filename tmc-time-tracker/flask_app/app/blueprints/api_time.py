# flask_app/app/blueprints/api_time.py

import json
import logging
from datetime import datetime, timedelta, time
from sqlalchemy.orm import joinedload
from sqlalchemy import func, cast, Date  # Added missing imports
import pytz
from flask import Blueprint, request, jsonify, current_app
from flask_babel import gettext as _
from pydantic import ValidationError

from .. import db
from ..models import (
    User, TimeEntry, BreakEntry, IdleEntry, CompanyConfig, 
    UserLocationLog, ActivityLog, OvertimeAllocation
)
from ..helpers import (
    _handle_stale_sessions_for_user, calculate_worked_hours, 
    finalize_idle_period, send_clock_out_email, get_programmatic_status,
    _get_consolidated_segments_for_user
)
from ..validation_models import ClockInPayload, ClockOutPayload

log = logging.getLogger(__name__)
api_time_bp = Blueprint('api_time', __name__)

# --- Timezone Configuration - FIXED: Don't access current_app at module level ---
def get_berlin_tz():
    """Get Berlin timezone from app config or return default."""
    try:
        return current_app.config.get('BERLIN_TZ', pytz.timezone('Europe/Berlin'))
    except RuntimeError:
        # Outside application context, return default
        return pytz.timezone('Europe/Berlin')


@api_time_bp.route('/clock_in_for_electron/<microsoft_oid>', methods=['POST'])
def clock_in_for_electron(microsoft_oid):
    """
    Clock in a user from Electron client.
    
    Path parameters:
        microsoft_oid: Microsoft OID of the user
    
    Expected payload:
        {
            "location": { ... },  # Optional geolocation data
            "client_version": "..."  # Optional client version
        }
    """
    user = User.query.filter_by(microsoft_oid=microsoft_oid).first_or_404()
    
    # Check suspension
    if user.is_suspended:
        return jsonify({
            'success': False, 
            'message': _('Your account is temporarily suspended.')
        }), 403

    config = CompanyConfig.query.first()
    now_utc = datetime.now(pytz.utc)
    berlin_tz = get_berlin_tz()
    now_berlin = now_utc.astimezone(berlin_tz)
    
    MIN_VALID_TIMESTAMP = datetime(2024, 1, 1, tzinfo=pytz.utc)

    # Check working hours start
    if config and config.working_hours_start and now_berlin.time() < config.working_hours_start:
        return jsonify({
            'success': False, 
            'message': _('Clock-in is only allowed from %(time)s onwards.', 
                        time=config.working_hours_start.strftime('%H:%M'))
        }), 403
            
    # Clean up stale sessions
    _handle_stale_sessions_for_user(user.id)

    today_berlin_date = now_berlin.date()
    start_of_today_berlin = berlin_tz.localize(datetime.combine(today_berlin_date, time.min))
    start_of_today_utc = start_of_today_berlin.astimezone(pytz.utc)

    # Check daily hour limit
    has_overtime_today = OvertimeAllocation.query.filter(
        OvertimeAllocation.user_id == user.id,
        OvertimeAllocation.date == today_berlin_date
    ).first()

    if not has_overtime_today:
        today_entries = TimeEntry.query.options(
            joinedload(TimeEntry.break_entries),
            joinedload(TimeEntry.idle_entries)
        ).filter(
            TimeEntry.user_id == user.id,
            TimeEntry.clock_in_time >= start_of_today_utc
        ).all()
        
        if sum(calculate_worked_hours(e) for e in today_entries) >= user.default_daily_hours:
            return jsonify({
                'success': False, 
                'message': 'You have reached your daily working hour limit.'
            }), 403

    # Check if already clocked in
    if TimeEntry.query.filter_by(user_id=user.id, clock_out_time=None).first():
        return jsonify({
            'success': False, 
            'message': 'You are already clocked in.'
        }), 409

    try:
        # Get last clock out for gap break creation
        last_clock_out_entry_today = TimeEntry.query.filter(
            TimeEntry.user_id == user.id,
            TimeEntry.clock_out_time.isnot(None),
            TimeEntry.clock_in_time >= start_of_today_utc
        ).order_by(TimeEntry.clock_out_time.desc()).first()
        
        data = request.get_json() or {}
        location_data = data.get('location')
        client_version = data.get('client_version')
        
        # Create new time entry
        new_entry = TimeEntry(
            user_id=user.id,
            clock_in_time=now_utc.replace(tzinfo=None),
            status='active',
            client_version=client_version
        )
        db.session.add(new_entry)
        db.session.flush()

        # Create gap break if needed
        if last_clock_out_entry_today:
            gap_start_time = last_clock_out_entry_today.clock_out_time
            gap_end_time = new_entry.clock_in_time
            
            if gap_start_time:
                gap_start_time_aware = gap_start_time.replace(tzinfo=pytz.utc)
            
            if (gap_start_time and gap_start_time_aware > MIN_VALID_TIMESTAMP and 
                (gap_end_time - gap_start_time).total_seconds() > 60):
                gap_break = BreakEntry(
                    time_entry_id=new_entry.id,
                    break_start_time=gap_start_time,
                    break_end_time=gap_end_time,
                    reason=_("Automatic break created between sessions.")
                )
                db.session.add(gap_break)
                log.info(f"Created automatic break for user {user.id}")

        # Log location if provided
        if location_data:
            new_location_log = UserLocationLog(
                user_id=user.id,
                time_entry_id=new_entry.id,
                ip_address=location_data.get('ip'),
                country=location_data.get('country'),
                city=location_data.get('city'),
                region=location_data.get('region')
            )
            db.session.add(new_location_log)

        # Log activity
        new_activity_log = ActivityLog(
            user_id=user.id,
            time_entry_id=new_entry.id,
            timestamp=new_entry.clock_in_time,
            event_type='clock_in',
            details=json.dumps({'source': 'electron_app', 'version': client_version})
        )
        db.session.add(new_activity_log)

        db.session.commit()
        log.info(f"User {user.username} (ID: {user.id}) clocked in.")
        return jsonify({'success': True, 'message': 'Clocked in successfully!'}), 200

    except Exception as e:
        db.session.rollback()
        log.error(f"Error clocking in: {e}", exc_info=True)
        return jsonify({'success': False, 'message': f'Server error: {str(e)}'}), 500
          

@api_time_bp.route('/clock_out_for_electron/<string:microsoft_oid>', methods=['POST'])
def clock_out_for_electron(microsoft_oid):
    """
    Clock out a user from Electron client.
    
    Path parameters:
        microsoft_oid: Microsoft OID of the user
    
    Expected payload:
        {
            "source": "manual|auto|long_break",
            "timestamp": "2024-01-01T12:00:00Z"  # Optional client timestamp
        }
    """
    try:
        payload_data = request.get_json(force=True)
        payload = ClockOutPayload.model_validate(payload_data)
    except (ValidationError, json.JSONDecodeError) as e:
        return jsonify({
            'success': False, 
            'message': 'Invalid input.', 
            'errors': str(e)
        }), 400

    user = User.query.filter_by(microsoft_oid=microsoft_oid).first_or_404()
    active_entry = TimeEntry.query.filter_by(user_id=user.id, clock_out_time=None).first()

    if not active_entry:
        log.info(f"Clock-out request for already clocked-out user {user.username}")
        return jsonify({
            'success': True, 
            'message': _('Already clocked out.')
        }), 200

    # Determine clock-out time (client timestamp or server time)
    clock_out_time_utc = datetime.now(pytz.utc)
    client_timestamp_str = payload_data.get('timestamp')
    if client_timestamp_str:
        try:
            clock_out_time_utc = datetime.fromisoformat(
                client_timestamp_str.replace('Z', '+00:00')
            )
            log.info(f"Using client timestamp for clock-out: {clock_out_time_utc}")
        except (ValueError, TypeError):
            log.warning(f"Could not parse client timestamp, using server time.")
    
    # End active breaks
    for b in BreakEntry.query.filter_by(time_entry_id=active_entry.id, break_end_time=None).all():
        b.break_end_time = clock_out_time_utc.replace(tzinfo=None)
    
    # End active idle periods
    for i in IdleEntry.query.filter_by(time_entry_id=active_entry.id, idle_end_time=None).all():
        finalize_idle_period(i, clock_out_time_utc, reason=_("Clock-out"))

    # Update time entry
    active_entry.clock_out_time = clock_out_time_utc.replace(tzinfo=None)
    
    # Set status based on source
    if payload.source == 'auto':
        active_entry.status = 'auto_clocked_out'
    elif payload.source == 'long_break':
        active_entry.status = 'auto_clocked_out_long_break'
    else:
        active_entry.status = 'completed'
        
    active_entry.total_worked_hours = calculate_worked_hours(active_entry)
    
    # Send email notification for auto clock-outs
    if payload.source == 'auto':
        send_clock_out_email(user, active_entry)

    # Log activity
    db.session.add(ActivityLog(
        user_id=user.id,
        time_entry_id=active_entry.id,
        timestamp=clock_out_time_utc.replace(tzinfo=None),
        event_type='clock_out',
        details=json.dumps({'source': payload.source})
    ))
    
    db.session.commit()
    return jsonify({'success': True, 'message': _('Clocked out successfully!')}), 200


@api_time_bp.route('/dashboard_data_for_electron/<int:user_id>', methods=['GET'])
def dashboard_data_for_electron(user_id):
    """
    Get dashboard data for Electron client.
    
    Path parameters:
        user_id: User ID
    
    Returns comprehensive dashboard state including current status,
    daily summary, and automation config.
    """
    user = User.query.get_or_404(user_id)
    _handle_stale_sessions_for_user(user.id)

    now_utc = datetime.now(pytz.utc)
    today_utc_date = now_utc.date()
    config = CompanyConfig.query.first()

    # Fetch all of today's entries with relations in one query
    today_entries = TimeEntry.query.options(
        joinedload(TimeEntry.break_entries),
        joinedload(TimeEntry.idle_entries)
    ).filter(
        TimeEntry.user_id == user.id,
        cast(TimeEntry.clock_in_time, Date) == today_utc_date
    ).order_by(TimeEntry.clock_in_time.desc()).all()

    # Find active entry
    active_entry = next((e for e in today_entries if e.clock_out_time is None), None)

    # Get programmatic status
    status = get_programmatic_status(user.id, active_entry)
    
    # Get segments
    session_segments = []
    if today_entries:
        session_segments = _get_consolidated_segments_for_user(
            user.id, today_utc_date, today_utc_date
        ) or []

    # Calculate totals in memory
    total_gross_seconds = 0
    total_break_seconds = 0
    total_idle_seconds = 0

    for e in today_entries:
        end_time = e.clock_out_time or now_utc.replace(tzinfo=None)
        total_gross_seconds += (end_time - e.clock_in_time).total_seconds()

        for b in e.break_entries:
            b_end = b.break_end_time or now_utc.replace(tzinfo=None)
            total_break_seconds += (b_end - b.break_start_time).total_seconds()
        
        for i in e.idle_entries:
            if i.reason != "In Meeting":
                i_end = i.idle_end_time or now_utc.replace(tzinfo=None)
                total_idle_seconds += (i_end - i.idle_start_time).total_seconds()

    daily_summary = {
        'date': today_utc_date.strftime('%Y-%m-%d'),
        'total_break_seconds': total_break_seconds,
        'total_idle_seconds': total_idle_seconds,
        'net_worked_hours': round(
            max(0, total_gross_seconds - total_break_seconds - total_idle_seconds) / 3600, 2
        )
    }

    # Extract active states
    active_break_entry = None
    active_idle_entry = None
    
    if active_entry:
        active_break_entry = next(
            (b for b in active_entry.break_entries if b.break_end_time is None), None
        )
        active_idle_entry = next(
            (i for i in active_entry.idle_entries if i.idle_end_time is None), None
        )

    # Automation config
    default_config = {
        'max_idle_minutes': 5,
        'idle_to_break_minutes': 10,
        'long_break_prompt_minutes': 90,
        'auto_clock_out_after_break_minutes': 120
    }
    
    automation_config = default_config
    if config:
        automation_config = {
            'max_idle_minutes': config.max_idle_minutes,
            'idle_to_break_minutes': config.idle_to_break_minutes,
            'long_break_prompt_minutes': config.long_break_prompt_minutes,
            'auto_clock_out_after_break_minutes': config.auto_clock_out_after_break_minutes
        }

    return jsonify({
        'status': status,
        'is_clocked_in': active_entry is not None,
        'user_display_name': user.username,
        'session_segments': session_segments,
        'server_time_utc': now_utc.isoformat(),
        'is_break_active': status == 'On Break',
        'is_idle_active': status == 'Idle',
        'is_in_meeting': status == 'In Meeting',
        'clock_in_time': pytz.utc.localize(active_entry.clock_in_time).isoformat() if active_entry else None,
        'current_ongoing_break_start_time': pytz.utc.localize(active_break_entry.break_start_time).isoformat() if active_break_entry else None,
        'current_ongoing_idle_start_time': pytz.utc.localize(active_idle_entry.idle_start_time).isoformat() if active_idle_entry and status == 'Idle' else None,
        'session_total_completed_break_seconds': daily_summary['total_break_seconds'],
        'session_total_completed_idle_seconds': daily_summary['total_idle_seconds'],
        'active_idle_entry_id': active_idle_entry.id if active_idle_entry else None,
        'automation_config': automation_config,
        'daily_summary': daily_summary,
        'can_clock_in': not active_entry
    })