# flask_app/app/blueprints/api_time.py

import json
import logging
from datetime import datetime, timedelta, time
from sqlalchemy.orm import joinedload
from sqlalchemy import or_
import pytz
from flask import Blueprint, request, jsonify, current_app
from flask_babel import gettext as _
from pydantic import ValidationError

from .. import db
from ..models import (
    User, TimeEntry, BreakEntry, IdleEntry, CompanyConfig, 
    UserLocationLog, ActivityLog, OvertimeAllocation, UserDeviceSession
)
from ..helpers import (
    _handle_stale_sessions_for_user, calculate_worked_hours, 
    finalize_idle_period, send_clock_out_email, get_programmatic_status,
    _get_consolidated_segments_for_user, calculate_entries_time_totals
)
from ..validation_models import ClockInPayload, ClockOutPayload
from ..helpers.schedule import get_user_schedule, is_within_user_schedule

log = logging.getLogger(__name__)
api_time_bp = Blueprint('api_time', __name__)
ACTIVE_DEVICE_TIMEOUT = timedelta(minutes=3)

# --- Timezone Configuration - FIXED: Don't access current_app at module level ---
def get_berlin_tz():
    """Get Berlin timezone from app config or return default."""
    try:
        return current_app.config.get('BERLIN_TZ', pytz.timezone('Europe/Berlin'))
    except RuntimeError:
        # Outside application context, return default
        return pytz.timezone('Europe/Berlin')


def _validate_current_device(user):
    device_id = str(
        request.headers.get('X-Device-ID')
        or (request.get_json(silent=True) or {}).get('device_id')
        or ''
    ).strip()

    if not device_id:
        return None, (jsonify({
            'code': 'E_DEVICE_REQUIRED',
            'message': 'A device ID is required.'
        }), 400)

    if user.device_mac_address != device_id:
        return None, (jsonify({
            'code': 'E_SESSION_REPLACED',
            'message': 'This desktop session is no longer active.'
        }), 409)

    device_session = UserDeviceSession.query.filter_by(
        user_id=user.id,
        device_id=device_id
    ).first()
    cutoff = datetime.now(pytz.utc).replace(tzinfo=None) - ACTIVE_DEVICE_TIMEOUT

    if not device_session or not device_session.last_heartbeat \
            or device_session.last_heartbeat < cutoff:
        return None, (jsonify({
            'code': 'E_DEVICE_SESSION_EXPIRED',
            'message': 'The desktop session has expired. Please sign in again.'
        }), 409)

    return device_session, None


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

    device_session, device_error = _validate_current_device(user)
    if device_error:
        return device_error
    
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

    schedule_start, schedule_end, working_days = get_user_schedule(user, config)
    if now_berlin.strftime('%A') not in working_days:
        return jsonify({
            'success': False,
            'code': 'E_OUTSIDE_WORKING_DAY',
            'message': _('Clock-in is not allowed on this working day.')
        }), 403

    if not is_within_user_schedule(user, config, now_berlin):
        return jsonify({
            'success': False,
            'code': 'E_OUTSIDE_WORKING_HOURS',
            'message': _('Clock-in is only allowed between %(start)s and %(end)s.',
                         start=schedule_start.strftime('%H:%M'),
                         end=schedule_end.strftime('%H:%M'))
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
        is_automatic = bool(data.get('automatic'))
        activity_proof = data.get('activity_proof') or {}

        if is_automatic:
            meaningful_event = activity_proof.get('event_type') in {
                'keydown', 'mousedown', 'wheel'
            }
            try:
                system_idle_seconds = float(activity_proof.get('system_idle_seconds'))
            except (TypeError, ValueError):
                system_idle_seconds = None

            if not meaningful_event or system_idle_seconds is None \
                    or system_idle_seconds > 30:
                return jsonify({
                    'success': False,
                    'code': 'E_ACTIVITY_REQUIRED',
                    'message': _('Automatic clock-in requires recent user activity.')
                }), 403
        
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
            details=json.dumps({
                'source': 'electron_app_auto' if is_automatic else 'electron_app_manual',
                'version': client_version,
                'device_id': device_session.device_id,
                'activity_proof': activity_proof if is_automatic else None
            })
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
    validated_device_session, device_error = _validate_current_device(user)
    if device_error:
        return device_error

    active_entry = TimeEntry.query.filter_by(user_id=user.id, clock_out_time=None).first()

    if not active_entry:
        log.info(f"Clock-out request for already clocked-out user {user.username}")
        return jsonify({
            'success': True, 
            'message': _('Already clocked out.')
        }), 200

    # Server time is authoritative. Client timestamps are telemetry only.
    clock_out_time_utc = datetime.now(pytz.utc)
    client_timestamp_str = payload_data.get('timestamp')
    
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
        details=json.dumps({
            'source': payload.source,
            'client_timestamp': client_timestamp_str
        })
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
    berlin_tz = get_berlin_tz()
    today_berlin = now_utc.astimezone(berlin_tz).date()
    day_start_utc = berlin_tz.localize(
        datetime.combine(today_berlin, time.min)
    ).astimezone(pytz.utc)
    day_end_utc = berlin_tz.localize(
        datetime.combine(today_berlin + timedelta(days=1), time.min)
    ).astimezone(pytz.utc)
    config = CompanyConfig.query.first()

    # Fetch all of today's entries with relations in one query
    today_entries = TimeEntry.query.options(
        joinedload(TimeEntry.break_entries),
        joinedload(TimeEntry.idle_entries)
    ).filter(
        TimeEntry.user_id == user.id,
        TimeEntry.clock_in_time < day_end_utc,
        or_(
            TimeEntry.clock_out_time.is_(None),
            TimeEntry.clock_out_time > day_start_utc
        )
    ).order_by(TimeEntry.clock_in_time.desc()).all()

    # Find active entry
    active_entry = next((e for e in today_entries if e.clock_out_time is None), None)

    # Get programmatic status
    status = get_programmatic_status(user.id, active_entry)
    
    # Get segments
    session_segments = []
    if today_entries:
        session_segments = _get_consolidated_segments_for_user(
            user.id, today_berlin, today_berlin
        ) or []

    totals = calculate_entries_time_totals(
        today_entries,
        now_utc=now_utc,
        window_start=day_start_utc,
        window_end=day_end_utc
    )

    daily_summary = {
        'date': today_berlin.strftime('%Y-%m-%d'),
        'total_break_seconds': totals['break_seconds'],
        'total_idle_seconds': totals['idle_seconds'],
        'total_pause_seconds': totals['pause_seconds'],
        'gross_worked_hours': round(totals['gross_seconds'] / 3600, 2),
        'net_worked_hours': round(totals['net_seconds'] / 3600, 2)
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
        'has_clocked_in_today': bool(today_entries),
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
