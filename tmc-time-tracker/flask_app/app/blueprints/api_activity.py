# flask_app/app/blueprints/api_activity.py

import json
import logging
from datetime import datetime, timedelta
from sqlalchemy import func, cast, Date  # Added missing imports
import pytz
from flask import Blueprint, request, jsonify, current_app
from flask_babel import gettext as _
from pydantic import ValidationError

from .. import db
from ..models import (
    User, TimeEntry, BreakEntry, IdleEntry, ActivityLog, 
    ApplicationUsage, UserDeviceSession
)
from ..helpers import finalize_idle_period
from ..validation_models import (
    ActivityPayload, ApplicationUsagePayload, FinalizeIdlePayload
)

log = logging.getLogger(__name__)
api_activity_bp = Blueprint('api_activity', __name__)

# --- Timezone Configuration - FIXED: Don't access current_app at module level ---
def get_berlin_tz():
    """Get Berlin timezone from app config or return default."""
    try:
        return current_app.config.get('BERLIN_TZ', pytz.timezone('Europe/Berlin'))
    except RuntimeError:
        # Outside application context, return default
        return pytz.timezone('Europe/Berlin')


@api_activity_bp.route('/start_idle_entry/<string:microsoft_oid>', methods=['POST'])
def start_idle_entry(microsoft_oid):
    """
    Start an idle period for a user.
    
    Path parameters:
        microsoft_oid: Microsoft OID of the user
    
    Uses multi-device consensus - if another device is active, idle may be suppressed.
    """
    user = User.query.filter_by(microsoft_oid=microsoft_oid).first_or_404()
    active_entry = TimeEntry.query.filter_by(user_id=user.id, clock_out_time=None).first()
    
    if not active_entry:
        return jsonify({'success': False, 'message': _('User not clocked in.')}), 400

    request_device_id = request.headers.get('X-Device-ID')
    now_utc = datetime.now(pytz.utc).replace(tzinfo=None)

    # --- 1. UPDATE THIS DEVICE'S STATE ---
    if request_device_id:
        device_session = UserDeviceSession.query.filter_by(
            user_id=user.id, device_id=request_device_id
        ).first()
        if device_session:
            device_session.is_idle = True
            device_session.last_heartbeat = now_utc
            db.session.commit()

    # --- 2. MULTI-DEVICE CONSENSUS CHECK ---
    active_cutoff = now_utc - timedelta(minutes=5)
    other_active_devices = UserDeviceSession.query.filter(
        UserDeviceSession.user_id == user.id,
        UserDeviceSession.device_id != request_device_id,
        UserDeviceSession.is_idle == False,
        UserDeviceSession.last_heartbeat >= active_cutoff
    ).count()

    if other_active_devices > 0:
        log.info(f"Idle request from device {request_device_id} suppressed; Remote activity detected.")
        return jsonify({
            'success': True,
            'message': 'Remote activity detected. Session remains active.',
            'suppress_idle': True,
            'id': None
        })

    # --- 3. APPLY GLOBAL IDLE ---
    existing_idle = IdleEntry.query.filter_by(
        time_entry_id=active_entry.id, idle_end_time=None
    ).first()
    
    if existing_idle:
        return jsonify({
            'success': True,
            'message': _('Already idle.'),
            'id': existing_idle.id
        })

    new_idle = IdleEntry(time_entry_id=active_entry.id, idle_start_time=now_utc)
    db.session.add(new_idle)
    db.session.commit()

    return jsonify({
        'success': True,
        'message': 'Idle period started.',
        'id': new_idle.id
    })


@api_activity_bp.route('/end_idle_entry/<int:idle_entry_id>', methods=['POST'])
def end_idle_entry(idle_entry_id):
    """
    End an idle period.
    
    Path parameters:
        idle_entry_id: ID of the idle entry to end
    """
    idle_entry = IdleEntry.query.get(idle_entry_id)
    if not idle_entry:
        return jsonify({'success': False, 'message': 'Idle entry not found'}), 404

    request_device_id = request.headers.get('X-Device-ID')
    now_utc = datetime.now(pytz.utc).replace(tzinfo=None)

    # --- 1. WAKE UP THIS DEVICE ---
    if request_device_id:
        user_id = idle_entry.time_entry.user_id
        device_session = UserDeviceSession.query.filter_by(
            user_id=user_id, device_id=request_device_id
        ).first()
        if device_session:
            device_session.is_idle = False
            device_session.last_heartbeat = now_utc
            db.session.commit()

    # --- 2. END THE IDLE ENTRY ---
    if idle_entry.idle_end_time is None:
        finalize_idle_period(idle_entry, now_utc)

    return jsonify({'success': True, 'message': 'Idle period ended.'})


@api_activity_bp.route('/finalize_idle_reason/<int:idle_entry_id>', methods=['POST'])
def finalize_idle_reason(idle_entry_id):
    """
    Finalize an idle entry with a reason.
    
    Path parameters:
        idle_entry_id: ID of the idle entry
    
    Expected payload:
        {
            "reason": "Lunch break"
        }
    """
    idle_entry = IdleEntry.query.get_or_404(idle_entry_id)
    
    try:
        payload = FinalizeIdlePayload.model_validate(request.get_json(force=True))
    except (ValidationError, json.JSONDecodeError) as e:
        return jsonify({'success': False, 'message': 'Invalid input.', 'errors': str(e)}), 400

    finalize_idle_period(idle_entry, datetime.now(pytz.utc), reason=payload.reason)
    return jsonify({'success': True})


@api_activity_bp.route('/record_application_usage', methods=['POST'])
def record_application_usage():
    """
    Record application usage data from Electron client.
    
    Expected payload:
        {
            "user_id": 123,
            "application_name": "chrome.exe",
            "window_title": "Google - Work",
            "start_time": "2024-01-01T10:00:00Z",
            "end_time": "2024-01-01T10:30:00Z",
            "duration_seconds": 1800,
            "device_id": "..."  # Optional
        }
    """
    # Capture raw JSON first
    try:
        json_data = request.get_json(force=True)
    except Exception:
        return jsonify({'success': False, 'message': 'Invalid JSON'}), 400

    # Extract Device ID (Priority: Payload > Header)
    device_id = json_data.get('device_id') or request.headers.get('X-Device-ID', 'unknown_device')

    # Validate payload
    try:
        payload = ApplicationUsagePayload.model_validate(json_data)
    except ValidationError as e:
        return jsonify({'success': False, 'message': 'Invalid input.', 'errors': e.errors()}), 400

    # Find active TimeEntry
    time_entry = TimeEntry.query.filter_by(
        user_id=payload.user_id,
        clock_out_time=None
    ).order_by(TimeEntry.clock_in_time.desc()).first()

    # Fallback to recently closed entry (within 5 minutes)
    if not time_entry:
        five_minutes_ago = datetime.now(pytz.utc) - timedelta(minutes=5)
        
        last_closed_entry = TimeEntry.query.filter(
            TimeEntry.user_id == payload.user_id,
            TimeEntry.clock_out_time.isnot(None),
            TimeEntry.clock_out_time >= five_minutes_ago.replace(tzinfo=None)
        ).order_by(TimeEntry.clock_out_time.desc()).first()

        if last_closed_entry:
            start_ts = payload.start_time.replace(tzinfo=None)
            if last_closed_entry.clock_in_time <= start_ts <= last_closed_entry.clock_out_time:
                time_entry = last_closed_entry
                log.info(f"Associating usage with closed TimeEntry ID: {time_entry.id}")

    time_entry_id = time_entry.id if time_entry else None

    # Find last usage for this device to potentially merge
    last_usage = None
    if time_entry_id:
        query = ApplicationUsage.query.filter_by(
            user_id=payload.user_id,
            time_entry_id=time_entry_id
        )
        
        # Filter by device ID
        if hasattr(ApplicationUsage, 'device_id'):
            query = query.filter_by(device_id=device_id)
            
        last_usage = query.order_by(ApplicationUsage.start_time.desc()).first()

    # Merge or create logic
    if (last_usage and 
        last_usage.application_name == payload.application_name and 
        last_usage.window_title == payload.window_title):
        
        last_usage.end_time = payload.end_time.replace(tzinfo=None)
        duration = (last_usage.end_time - last_usage.start_time).total_seconds()
        last_usage.duration_seconds = int(duration)
        db.session.add(last_usage)
    else:
        usage = ApplicationUsage(
            user_id=payload.user_id,
            time_entry_id=time_entry_id,
            application_name=payload.application_name,
            window_title=payload.window_title,
            start_time=payload.start_time.replace(tzinfo=None),
            end_time=payload.end_time.replace(tzinfo=None),
            duration_seconds=payload.duration_seconds
        )
        
        if hasattr(usage, 'device_id'):
            usage.device_id = device_id
            
        db.session.add(usage)

    db.session.commit()
    return jsonify({'success': True, 'message': 'Application usage recorded'})


@api_activity_bp.route('/activity', methods=['POST'])
def record_activity():
    """
    Record user activity state (active/idle) with device tracking.
    
    Expected payload:
        {
            "user_id": 123,
            "type": "active|idle|screen_locked|screen_unlocked|system_suspend|system_resumed|user_input",
            "timestamp": "2024-01-01T10:00:00Z",
            "metadata": {},  # Optional
            "device_id": "..."  # Optional
        }
    """
    try:
        json_data = request.get_json(force=True)
        payload = ActivityPayload.model_validate(json_data)
    except ValidationError as e:
        return jsonify({'success': False, 'message': 'Invalid input.', 'errors': e.errors()}), 400

    # Check user existence
    user = User.query.get(payload.user_id)
    if not user:
        return jsonify({'success': False, 'message': _('User not found')}), 404

    # Capture Device ID
    device_id = getattr(payload, 'device_id', None) or \
                json_data.get('device_id') or \
                request.headers.get('X-Device-ID', 'unknown_device')

    # -----------------------------------------------------------------------
    # Update Live Device Session State
    # -----------------------------------------------------------------------
    if device_id and device_id != 'unknown_device':
        session = UserDeviceSession.query.filter_by(
            user_id=user.id,
            device_id=device_id
        ).first()

        if not session:
            session = UserDeviceSession(user_id=user.id, device_id=device_id)
            db.session.add(session)

        session.last_heartbeat = datetime.utcnow()

        # Update IDLE state based on event type
        active_signals = ['active', 'system_resumed', 'screen_unlocked', 'user_input']
        idle_signals = ['idle', 'system_suspend', 'screen_locked']

        if payload.type in active_signals:
            session.is_idle = False
        elif payload.type in idle_signals:
            session.is_idle = True

    # -----------------------------------------------------------------------
    # Historical Logging
    # -----------------------------------------------------------------------
    active_entry = TimeEntry.query.filter_by(user_id=user.id, clock_out_time=None).first()
    
    if active_entry:
        now_utc = datetime.utcnow()

        # Prepare JSON details
        details_data = {
            'timestamp': payload.timestamp.isoformat() if payload.timestamp else str(now_utc),
            'device_id': device_id,
            'source': 'electron_app',
            'metadata': payload.metadata or {}
        }

        activity_log = ActivityLog(
            user_id=user.id,
            time_entry_id=active_entry.id,
            timestamp=now_utc,
            event_type=payload.type,
            details=json.dumps(details_data)
        )
        
        db.session.add(activity_log)
        
        # Debug logging
        if payload.type in ['system_resumed', 'screen_unlocked', 'active']:
            log.info(f"Activity 'ACTIVE' for user {user.email} on device {device_id}")
        elif payload.type in ['system_suspend', 'screen_locked', 'idle']:
            log.info(f"Activity 'IDLE' for user {user.email} on device {device_id}")

    # Commit transaction
    try:
        db.session.commit()
        return jsonify({'success': True, 'message': f'Activity {payload.type} recorded.'}), 200
    except Exception as e:
        db.session.rollback()
        log.error(f"Database error in record_activity: {e}")
        return jsonify({'success': False, 'message': 'Database error'}), 500


@api_activity_bp.route('/heartbeat_ping/<string:microsoft_oid>', methods=['POST'])
def receive_heartbeat_ping(microsoft_oid):
    """
    Receive heartbeat ping from Electron client.
    
    Path parameters:
        microsoft_oid: Microsoft OID of the user
    
    Updates device session and global user heartbeat.
    """
    payload = request.get_json(silent=True) or {}
    device_id = payload.get('device_id')

    user = User.query.filter_by(microsoft_oid=microsoft_oid).first()
    if not user:
        return jsonify({"message": "User not found"}), 404

    # Check if user has an open session
    active_entry = TimeEntry.query.filter_by(
        user_id=user.id,
        clock_out_time=None
    ).first()

    if not active_entry:
        log.warning(f"Heartbeat ping from clocked-out user: {user.email}")
        return jsonify({"message": "User is not clocked in on server."}), 409

    now_utc = datetime.utcnow()

    # Update specific device session
    if device_id:
        device_session = UserDeviceSession.query.filter_by(
            user_id=user.id,
            device_id=device_id
        ).first()

        if not device_session:
            device_session = UserDeviceSession(user_id=user.id, device_id=device_id)
            db.session.add(device_session)

        device_session.last_heartbeat = now_utc

    # Update global user heartbeat
    user.last_heartbeat_utc = now_utc

    db.session.commit()
    return jsonify({"message": "Heartbeat ping received"}), 200


@api_activity_bp.route('/heartbeat', methods=["POST"])
def heartbeat_cleanup_job():
    """
    Heartbeat cleanup job - puts users on break if no activity detected.
    This should be called by a scheduler (e.g., cron job).
    """
    log.info("Scheduler: Running heartbeat cleanup job...")
    now_utc = datetime.utcnow()
    threshold = now_utc - timedelta(minutes=5)

    active_entries = TimeEntry.query.filter(TimeEntry.clock_out_time.is_(None)).all()
    breaks_started_count = 0

    for entry in active_entries:
        user_id = entry.user_id

        # Skip if in meeting
        active_meeting = next((i for i in entry.idle_entries if i.idle_end_time is None and i.reason == "In Meeting"), None)
        if active_meeting:
            continue

        # Skip if already on break
        active_break = next((b for b in entry.break_entries if b.break_end_time is None), None)
        if active_break:
            continue

        # Check for recent activity
        last_usage = ApplicationUsage.query.filter_by(user_id=user_id)\
            .order_by(ApplicationUsage.end_time.desc()).first()
        ts_usage = last_usage.end_time or last_usage.start_time if last_usage else None

        last_activity = ActivityLog.query.filter_by(user_id=user_id)\
            .order_by(ActivityLog.timestamp.desc()).first()
        ts_activity = last_activity.timestamp if last_activity else None

        # Check device heartbeats
        last_device_ping = db.session.query(func.max(UserDeviceSession.last_heartbeat))\
            .filter_by(user_id=user_id).scalar()

        ts_heartbeat = last_device_ping or entry.user.last_heartbeat_utc

        # Determine last seen time
        timestamps = [t for t in [ts_usage, ts_activity, ts_heartbeat] if t is not None]

        last_seen = max(timestamps) if timestamps else None

        # If no activity, start a break
        if not last_seen or last_seen < threshold:
            new_break = BreakEntry(
                time_entry_id=entry.id,
                break_start_time=now_utc,
                reason="System Idle (No Heartbeat)"
            )
            db.session.add(new_break)
            breaks_started_count += 1
            log.info(f"[HEARTBEAT] Putting user {entry.user.username} on break.")

    if breaks_started_count > 0:
        db.session.commit()

    return jsonify({
        "message": f"Heartbeat cleanup finished. Breaks started: {breaks_started_count}"
    }), 200


# --- ACTIVITY SEGMENT ROUTES (Keep both for different clients) ---

@api_activity_bp.route('/electron/user_activity_segments', methods=['GET'])
def electron_user_activity_segments_api():
    """
    Activity segments endpoint for Electron client.
    
    Query parameters:
        user_id: User ID
        start_date: YYYY-MM-DD
    """
    user_id = request.args.get('user_id', type=int)
    start_date_str = request.args.get('start_date')

    if not all([user_id, start_date_str]):
        return jsonify({'success': False, 'message': 'Missing required parameters.'}), 400

    try:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'success': False, 'message': 'Invalid date format.'}), 400

    from ..helpers import _get_consolidated_segments_for_user
    raw_segments = _get_consolidated_segments_for_user(user_id, start_date, start_date)
    return jsonify({'success': True, 'segments': raw_segments or []})


@api_activity_bp.route('/get_activity_segments/<int:user_id>', methods=['GET'])
def get_activity_segments(user_id):
    """
    Activity segments endpoint for web dashboard.
    
    Path parameters:
        user_id: User ID
    
    Query parameters:
        date: YYYY-MM-DD
    """
    date_str = request.args.get('date')
    if not date_str:
        return jsonify({'success': False, 'message': 'Date parameter is required.'}), 400

    try:
        target_date = datetime.strptime(date_str, '%Y-%m-%d').date()  # Fixed: was '%Y-%m-%'
    except ValueError:
        return jsonify({'success': False, 'message': 'Invalid date format. Use YYYY-MM-DD.'}), 400

    from ..helpers import _get_consolidated_segments_for_user
    segments = _get_consolidated_segments_for_user(user_id, target_date, target_date)

    # If user is clocked in but no segments, create default "Active" segment
    if not segments:
        active_entry = TimeEntry.query.filter(
            TimeEntry.user_id == user_id,
            TimeEntry.clock_out_time.is_(None),
            cast(TimeEntry.clock_in_time, Date) == target_date
        ).first()
        if active_entry:
            now_utc = datetime.now(pytz.utc)
            entry_start_time_utc = pytz.utc.localize(active_entry.clock_in_time)
            segments.append({
                'start_time': entry_start_time_utc.isoformat(),
                'end_time': now_utc.isoformat(),
                'status': 'Active',
                'notes': 'Working'
            })

    return jsonify({'success': True, 'segments': segments})