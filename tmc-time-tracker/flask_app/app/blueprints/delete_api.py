# /flask_app/app/blueprints/api.py

import json
import logging
import secrets
import time as time_module
from datetime import datetime, timedelta, date, time
import pytz
from collections import defaultdict
from sqlalchemy.orm import joinedload
from flask import Blueprint, request, jsonify
from flask_babel import gettext as _
from sqlalchemy import func, text, cast, Date, literal_column
from flask_login import current_user

from .. import db
from ..models import (User, TimeEntry, BreakEntry, IdleEntry, CompanyConfig,
                    UserLocationLog, OvertimeAllocation, ActivityLog, ApplicationUsage, UserDeviceSession, AutomatedStateChange)
from ..helpers import (is_user_globally_active, utc_to_berlin_filter, _handle_stale_sessions_for_user, finalize_idle_period,
                     calculate_worked_hours, send_clock_out_email, _get_consolidated_segments_for_user,
                     format_duration_filter, get_programmatic_status, find_and_clock_out_zombie_sessions) 
from ..validation_models import (SsoLoginPayload, OidPayload, ClockInPayload, ClockOutPayload,
                               FinalizeIdlePayload, ActivityPayload, SystemSleepPayload,
                               WebTokenPayload, ApplicationUsagePayload, StateChangePayload, BreakReasonPayload)
from .auth import sso_tokens
from pydantic import ValidationError
from flask_login import login_required
from ..decorators import admin_required, require_auth
from sqlalchemy.sql import case

import logging
log = logging.getLogger(__name__)

api_bp = Blueprint('api', __name__)

# --- API Routes for Electron App ---
# --- Timezone Configuration ---
BERLIN_TZ = pytz.timezone('Europe/Berlin')

@api_bp.route('/v1/config/<int:user_id>', methods=['GET'])
def get_user_config(user_id):
    try:
        # 1. Fetch User (Safe)
        user = User.query.get(user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404

        # 2. Fetch Config (Safe - Handle Missing or Empty)
        config = CompanyConfig.query.first()

        # --- Defaults (if DB empty or NULL) ---
        effective_start_time = time(8, 0)   # 08:00
        effective_end_time = time(17, 0)    # 17:00

        # --- SAFE SQL SERVER TIME PARSING ---
        if config:

            # ---------- START TIME ----------
            raw_start = config.working_hours_start
            if raw_start:
                if isinstance(raw_start, str):
                    # e.g. "04:00:00.0000000"
                    effective_start_time = datetime.strptime(
                        raw_start.split('.')[0],
                        "%H:%M:%S"
                    ).time()
                elif isinstance(raw_start, datetime):
                    # SQL Server sometimes returns datetime 1900-01-01 04:00
                    effective_start_time = raw_start.time()
                else:
                    # already datetime.time
                    effective_start_time = raw_start

            # ---------- END TIME ----------
            raw_end = config.working_hours_end
            if raw_end:
                if isinstance(raw_end, str):
                    effective_end_time = datetime.strptime(
                        raw_end.split('.')[0],
                        "%H:%M:%S"
                    ).time()
                elif isinstance(raw_end, datetime):
                    effective_end_time = raw_end.time()
                else:
                    effective_end_time = raw_end

        # 3. Handle Timezones
        now_utc = datetime.now(pytz.utc)
        now_berlin = now_utc.astimezone(BERLIN_TZ)
        today = now_berlin.date()

        # 4. Overtime Allocation (Override End Time Only)
        overtime_allocation = OvertimeAllocation.query.filter(
            OvertimeAllocation.user_id == user_id,
            OvertimeAllocation.date == today
        ).first()

        if overtime_allocation and overtime_allocation.end_time:
            raw_ot_end = overtime_allocation.end_time
            if isinstance(raw_ot_end, str):
                effective_end_time = datetime.strptime(
                    raw_ot_end.split('.')[0],
                    "%H:%M:%S"
                ).time()
            elif isinstance(raw_ot_end, datetime):
                effective_end_time = raw_ot_end.time()
            else:
                effective_end_time = raw_ot_end

        # 5. Return JSON
        return jsonify({
            'startTime': effective_start_time.strftime('%H:%M:%S'),
            'endTime': effective_end_time.strftime('%H:%M:%S'),
            'isSuspended': bool(user.is_suspended),
            'serverTime': now_utc.isoformat(),
            'requireLocation': getattr(config, 'require_location', True) if config else True,
            'uploadInterval': getattr(config, 'upload_interval_seconds', 60) if config else 60
        })

    except Exception as e:
        print(f"ERROR in get_user_config: {str(e)}")
        return jsonify({
            'startTime': '09:00:00',
            'endTime': '17:00:00',
            'isSuspended': False,
            'serverTime': datetime.utcnow().isoformat() + 'Z'
        }), 200

@api_bp.route('/electron_sso_login', methods=['POST'])
def electron_sso_login():
    try:
        # We get the raw json to extract mac_address, as pydantic model might not have it yet
        payload_data = request.get_json(force=True)
        payload = SsoLoginPayload.model_validate(payload_data)
        # Get mac_address from the raw payload (This can be MAC or MachineGuid UUID)
        client_device_id = payload_data.get('mac_address') 
    except (ValidationError, json.JSONDecodeError) as e:
        return jsonify({'success': False, 'message': 'Invalid input.', 'errors': str(e)}), 400

    try:
        ALLOWED_DOMAIN = '@tm-connect.de'
        if not str(payload.email).lower().endswith(ALLOWED_DOMAIN):
            return jsonify({'success': False, 'message': 'Access denied for domain.'}), 403

        # Find or create user
        user = User.query.filter_by(microsoft_oid=payload.microsoft_oid).first()
        if not user:
            user = User.query.filter_by(email=str(payload.email)).first()

        if user:
            log.info(f"Existing user '{user.username}' found. Syncing profile info.")
            user.microsoft_oid = payload.microsoft_oid
            user.username = payload.username
            
            # --- NEW DEVICE LOCK LOGIC (Handles MAC or MachineGUID) ---
            if client_device_id:
                # 1. Check if Admin - Grant Passivity
                if user.is_admin:
                    log.info(f"Admin '{user.username}' logging in. Bypassing device lock check for ID: '{client_device_id}'.")
                    # Note: We do NOT update user.device_mac_address here to avoid UniqueConstraint violations
                    # if the admin uses a device already registered to a standard employee.

                # 2. Standard Employee Logic
                else:
                    if not user.device_mac_address:
                        # First login on a new client, register this device ID
                        log.info(f"Registering new device ID '{client_device_id}' for user '{user.username}'.")
                        user.device_mac_address = client_device_id
                    elif user.device_mac_address != client_device_id:
                        # ID mismatch, deny login
                        log.warning(f"Device mismatch for user '{user.username}'. Stored: '{user.device_mac_address}', Attempted: '{client_device_id}'.")
                        return jsonify({
                            'success': False, 
                            'message': 'This account is locked to a different device. Please contact your administrator.'
                        }), 403
                    # If ID matches, login is allowed
            else:
                # Client did not send a ID (e.g., old version or failed fetch)
                if user.device_mac_address and not user.is_admin:
                    # A device is registered and user is NOT admin, so we MUST enforce the check.
                    log.warning(f"User '{user.username}' has a registered device, but client sent no ID. Denying.")
                    return jsonify({
                        'success': False, 
                        'message': 'This client app is outdated. Please update. (Error: ID_REQUIRED)'
                    }), 400
                
                # If no ID registered (or user is admin) and no ID sent, allow login.
                log.info(f"No Device ID received for user '{user.username}'. Login allowed (Admin or No Device Registered).")
            # --- END NEW DEVICE LOCK LOGIC ---

        else:
            log.info(f"Creating new user '{payload.username}'.")
            config = CompanyConfig.query.first()
            user = User(
                username=payload.username,
                email=str(payload.email),
                microsoft_oid=payload.microsoft_oid,
                # --- NEW: Register Device ID on creation ---
                device_mac_address=client_device_id, 
                # --- END NEW ---
                default_daily_hours=config.default_daily_hours if config else 8.0,
                default_working_days=config.default_working_days if config else 'Monday,Tuesday,Wednesday,Thursday,Friday'
            )
            db.session.add(user)

        db.session.commit()
        return jsonify({'success': True, 'user_id': user.id, 'username': user.username, 'email': str(payload.email)}), 200

    except Exception as e:
        db.session.rollback()
        log.error(f"SSO login failed: {e}", exc_info=True)
        return jsonify({'success': False, 'message': f'Database error: {str(e)}'}), 500

@api_bp.route('/get_user_id_from_oid', methods=['POST'])
def get_user_id_from_oid():
    try:
        payload = OidPayload.model_validate(request.get_json(force=True))
    except (ValidationError, json.JSONDecodeError) as e:
        return jsonify({'error': 'Invalid input.', 'details': str(e)}), 400

    user = User.query.filter_by(microsoft_oid=payload.microsoft_oid).first()
    if user:
        return jsonify({'user_id': user.id}), 200
    return jsonify({'error': 'User not found'}), 404

@api_bp.route('/clock_in_for_electron/<microsoft_oid>', methods=['POST'])
def clock_in_for_electron(microsoft_oid):
    user = User.query.filter_by(microsoft_oid=microsoft_oid).first_or_404()
    
    if user.is_suspended:
        return jsonify({'success': False, 'message': _('Your account is temporarily suspended.')}), 403

    config = CompanyConfig.query.first()
    now_utc = datetime.now(pytz.utc)
    now_berlin = now_utc.astimezone(BERLIN_TZ)
    
    MIN_VALID_TIMESTAMP = datetime(2024, 1, 1, tzinfo=pytz.utc)

    if config and config.working_hours_start and now_berlin.time() < config.working_hours_start:
        return jsonify({
            'success': False, 
            'message': _('Clock-in is only allowed from %(time)s onwards.', time=config.working_hours_start.strftime('%H:%M'))
        }), 403
            
    _handle_stale_sessions_for_user(user.id)

    today_berlin_date = now_berlin.date()
    start_of_today_berlin = BERLIN_TZ.localize(datetime.combine(today_berlin_date, time.min))
    start_of_today_utc = start_of_today_berlin.astimezone(pytz.utc)

    has_overtime_today = OvertimeAllocation.query.filter(
    OvertimeAllocation.user_id == user.id,
    OvertimeAllocation.date == today_berlin_date
).first()

    if not has_overtime_today:
        # --- FIX: Eager load relations to prevent N+1 during worked hours calculation ---
        today_entries = TimeEntry.query.options(
            joinedload(TimeEntry.break_entries),
            joinedload(TimeEntry.idle_entries)
        ).filter(
            TimeEntry.user_id == user.id,
            TimeEntry.clock_in_time >= start_of_today_utc
        ).all()
        # --- END FIX ---
        
        if sum(calculate_worked_hours(e) for e in today_entries) >= user.default_daily_hours:
            return jsonify({'success': False, 'message': 'You have reached your daily working hour limit.'}), 403

    if TimeEntry.query.filter_by(user_id=user.id, clock_out_time=None).first():
        return jsonify({'success': False, 'message': 'You are already clocked in.'}), 409

    try:
        last_clock_out_entry_today = TimeEntry.query.filter(
            TimeEntry.user_id == user.id,
            TimeEntry.clock_out_time.isnot(None),
            TimeEntry.clock_in_time >= start_of_today_utc
        ).order_by(TimeEntry.clock_out_time.desc()).first()
        
        data = request.get_json() or {}
        location_data = data.get('location')
        client_version = data.get('client_version')
        
        new_entry = TimeEntry(
            user_id=user.id,
            clock_in_time=now_utc.replace(tzinfo=None), # This is guaranteed to be valid
            status='active',
            client_version=client_version
        )
        db.session.add(new_entry)
        db.session.flush() 

        if last_clock_out_entry_today:
            gap_start_time = last_clock_out_entry_today.clock_out_time
            gap_end_time = new_entry.clock_in_time # This is guaranteed to be valid
            
            if gap_start_time:
                gap_start_time_aware = gap_start_time.replace(tzinfo=pytz.utc)
            
            if gap_start_time and gap_start_time_aware > MIN_VALID_TIMESTAMP:
                # Timestamps are valid and recent. Now check the duration.
                if (gap_end_time - gap_start_time).total_seconds() > 60:
                    gap_break = BreakEntry(
                        time_entry_id=new_entry.id,
                        break_start_time=gap_start_time,
                        break_end_time=gap_end_time,
                        reason=_("Automatic break created between sessions.")
                    )
                    db.session.add(gap_break)
                    log.info(f"Created automatic break for user {user.id} for the gap between sessions.")
            else:
                # Log why we skipped creating a gap break
                if not gap_start_time:
                    log.warning(f"Did not create gap break for user {user.id} because gap_start_time was None.")
                else:
                    log.warning(f"Did not create gap break for user {user.id} because gap_start_time {gap_start_time_aware} was before MIN_VALID_TIMESTAMP.")

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

        new_activity_log = ActivityLog(
            user_id=user.id,
            time_entry_id=new_entry.id,
            timestamp=new_entry.clock_in_time,
            event_type='clock_in',
            details=json.dumps({'source': 'electron_app', 'version': client_version})
        )
        db.session.add(new_activity_log)

        db.session.commit()

        log.info(f"User {user.username} (ID: {user.id}) clocked in via Electron version {client_version}.")
        return jsonify({'success': True, 'message': 'Clocked in successfully!'}), 200

    except Exception as e:
        db.session.rollback()
        log.error(f"Error clocking in for OID {microsoft_oid}: {e}", exc_info=True)
        return jsonify({'success': False, 'message': f'Server error: {str(e)}'}), 500
          
@api_bp.route('/clock_out_for_electron/<string:microsoft_oid>', methods=['POST'])
def clock_out_for_electron(microsoft_oid):
    try:
        payload_data = request.get_json(force=True)
        payload = ClockOutPayload.model_validate(payload_data)
    except (ValidationError, json.JSONDecodeError) as e:
        return jsonify({'success': False, 'message': 'Invalid input.', 'errors': str(e)}), 400

    user = User.query.filter_by(microsoft_oid=microsoft_oid).first_or_404()
    active_entry = TimeEntry.query.filter_by(user_id=user.id, clock_out_time=None).first()

    if not active_entry: 
        log.info(f"Received clock-out request for user {user.username}, but they were already clocked out. Ignoring.")
        return jsonify({'success': True, 'message': _('Already clocked out.')}), 200

    # [FIX 1] Use timestamp from payload if available, otherwise use current time
    clock_out_time_utc = datetime.now(pytz.utc)
    client_timestamp_str = payload_data.get('timestamp') 
    if client_timestamp_str:
        try:
            # Parse ISO 8601 format sent from Electron
            clock_out_time_utc = datetime.fromisoformat(client_timestamp_str.replace('Z', '+00:00'))
            log.info(f"Using client-provided timestamp for clock-out: {clock_out_time_utc}")
        except (ValueError, TypeError):
            log.warning(f"Could not parse client timestamp '{client_timestamp_str}'. Defaulting to server time.")
    
    # End active breaks and idle periods using the determined clock-out time
    for b in BreakEntry.query.filter_by(time_entry_id=active_entry.id, break_end_time=None).all(): 
        b.break_end_time = clock_out_time_utc.replace(tzinfo=None)
    
    for i in IdleEntry.query.filter_by(time_entry_id=active_entry.id, idle_end_time=None).all(): 
        finalize_idle_period(i, clock_out_time_utc, reason=_("Clock-out"))

    # Update TimeEntry details
    active_entry.clock_out_time = clock_out_time_utc.replace(tzinfo=None)
    
    # Determine status based on source
    if payload.source == 'auto':
        active_entry.status = 'auto_clocked_out'
    elif payload.source == 'long_break':
        active_entry.status = 'auto_clocked_out_long_break' # A more specific status
    else:
        active_entry.status = 'completed'
        
    active_entry.total_worked_hours = calculate_worked_hours(active_entry)
    
    if payload.source == 'auto': 
        send_clock_out_email(user, active_entry)

    db.session.add(ActivityLog(
        user_id=user.id, time_entry_id=active_entry.id, timestamp=clock_out_time_utc.replace(tzinfo=None),
        event_type='clock_out', details=json.dumps({'source': payload.source})
    ))
    
    db.session.commit()
    return jsonify({'success': True, 'message': _('Clocked out successfully!')}), 200

@api_bp.route('/start_break_for_electron/<string:microsoft_oid>', methods=['POST'])
def start_break_for_electron(microsoft_oid):
    user = User.query.filter_by(microsoft_oid=microsoft_oid).first_or_404()
    active_entry = TimeEntry.query.filter_by(user_id=user.id, clock_out_time=None).first()
    
    if not active_entry: 
        return jsonify({'success': False, 'message': _('Not clocked in.')}), 400

    request_device_id = request.headers.get('X-Device-ID')
    now_utc = datetime.now(pytz.utc).replace(tzinfo=None)

    # --- 1. UPDATE THIS SPECIFIC DEVICE'S STATE FIRST ---
    # A device going on break is effectively "idle" to the rest of the network
    if request_device_id:
        device_session = UserDeviceSession.query.filter_by(user_id=user.id, device_id=request_device_id).first()
        if device_session:
            device_session.is_idle = True
            device_session.last_heartbeat = now_utc
            db.session.commit()

    # --- 2. MULTI-DEVICE CONSENSUS CHECK ---
    # Are there ANY OTHER devices currently active?
    active_cutoff = now_utc - timedelta(minutes=5)
    other_active_devices = UserDeviceSession.query.filter(
        UserDeviceSession.user_id == user.id,
        UserDeviceSession.device_id != request_device_id,
        UserDeviceSession.is_idle == False,
        UserDeviceSession.last_heartbeat >= active_cutoff
    ).count()

    if other_active_devices > 0:
        log.info(f"Break request from device {request_device_id} suppressed; User is active on another device.")
        return jsonify({
            'success': True, 
            'message': 'Remote activity detected. Global break suppressed.',
            'suppress_break': True
        }), 200

    # --- 3. APPLY GLOBAL BREAK (Consensus Met) ---
    if BreakEntry.query.filter_by(time_entry_id=active_entry.id, break_end_time=None).first():
        return jsonify({'success': False, 'message': _('Already on a break.')}), 400

    # Finalize any open idle periods first (since we are upgrading to a Break)
    for i in IdleEntry.query.filter_by(time_entry_id=active_entry.id, idle_end_time=None).all():
        finalize_idle_period(i, now_utc, reason=_("Break started"))

    new_break = BreakEntry(time_entry_id=active_entry.id, break_start_time=now_utc)
    db.session.add(new_break)
    db.session.commit()
    
    return jsonify({'success': True, 'message': _('Break started.')}), 200


@api_bp.route('/end_break_for_electron/<string:microsoft_oid>', methods=['POST'])
def end_break_for_electron(microsoft_oid):
    user = User.query.filter_by(microsoft_oid=microsoft_oid).first_or_404()
    active_entry = TimeEntry.query.filter_by(user_id=user.id, clock_out_time=None).first()
    
    if not active_entry: 
        return jsonify({'success': False, 'message': _('Not clocked in.')}), 400

    request_device_id = request.headers.get('X-Device-ID')
    now_utc = datetime.now(pytz.utc).replace(tzinfo=None)

    # --- 1. WAKE UP THIS SPECIFIC DEVICE ---
    # The user interacted with this PC to end the break, so we must mark it active!
    if request_device_id:
        device_session = UserDeviceSession.query.filter_by(user_id=user.id, device_id=request_device_id).first()
        if device_session:
            device_session.is_idle = False
            device_session.last_heartbeat = now_utc
            db.session.commit()
    
    # --- 2. END THE GLOBAL BREAK ---
    open_breaks = BreakEntry.query.filter_by(time_entry_id=active_entry.id, break_end_time=None).all()
    
    if not open_breaks:
        return jsonify({'success': True, 'message': _('No active breaks found.')}), 200
    
    # Track the most recent break to return its ID for the prompt
    primary_break = open_breaks[0] 
    longest_duration_sec = 0
    
    for b in open_breaks:
        b.break_end_time = now_utc
        duration = (b.break_end_time - b.break_start_time).total_seconds()
        if duration > longest_duration_sec:
            longest_duration_sec = duration
            primary_break = b

    db.session.commit()

    # --- 3. Handle Long Break Prompt Logic ---
    config = CompanyConfig.query.first()
    prompt_for_reason = False
    
    if config and config.long_break_prompt_minutes:
        if longest_duration_sec > (config.long_break_prompt_minutes * 60):
            prompt_for_reason = True

    return jsonify({
        'success': True, 
        'message': _('Break ended.'), 
        'prompt_for_reason': prompt_for_reason, 
        'break_id': primary_break.id if prompt_for_reason else None
    }), 200

@api_bp.route('/submit_break_reason/<int:break_id>', methods=['POST'])
def submit_break_reason(break_id):
    payload = BreakReasonPayload.model_validate(request.get_json(force=True))
    break_entry = BreakEntry.query.get_or_404(break_id)
    break_entry.reason = payload.reason
    db.session.commit()
    return jsonify({'success': True, 'message': _('Reason submitted.')})

@api_bp.route('/start_meeting_for_electron/<string:microsoft_oid>', methods=['POST'])
def start_meeting_for_electron(microsoft_oid):
    user = User.query.filter_by(microsoft_oid=microsoft_oid).first_or_404()
    active_entry = TimeEntry.query.filter_by(user_id=user.id, clock_out_time=None).first()
    if not active_entry: return jsonify({'success': False, 'message': _('Not clocked in')}), 400
    
    # --- FIX: Check if already in a meeting ---
    existing_meeting = IdleEntry.query.filter_by(
        time_entry_id=active_entry.id, 
        idle_end_time=None, 
        reason="In Meeting"
    ).first()
    if existing_meeting:
        return jsonify({'success': True, 'message': 'Meeting mode is already active.'}), 200

    now_utc = datetime.now(pytz.utc)
    # End any ongoing breaks or idle periods before starting the meeting
    for b in BreakEntry.query.filter_by(time_entry_id=active_entry.id, break_end_time=None).all(): 
        b.break_end_time = now_utc.replace(tzinfo=None)
    for i in IdleEntry.query.filter_by(time_entry_id=active_entry.id, idle_end_time=None).all(): 
        finalize_idle_period(i, now_utc, reason=_("Meeting started"))

    # Create the new meeting entry
    db.session.add(IdleEntry(time_entry_id=active_entry.id, idle_start_time=now_utc.replace(tzinfo=None), reason="In Meeting"))
    db.session.commit()
    return jsonify({'success': True, 'message': 'Meeting mode started.'})

@api_bp.route('/end_meeting_for_electron/<string:microsoft_oid>', methods=['POST'])
def end_meeting_for_electron(microsoft_oid):
    user = User.query.filter_by(microsoft_oid=microsoft_oid).first_or_404()
    active_entry = TimeEntry.query.filter_by(user_id=user.id, clock_out_time=None).first()
    if not active_entry: return jsonify({'success': False, 'message': _('Not clocked in')}), 400

    meeting_idle = IdleEntry.query.filter_by(time_entry_id=active_entry.id, idle_end_time=None, reason="In Meeting").order_by(IdleEntry.idle_start_time.desc()).first()
    if not meeting_idle: return jsonify({'success': False, 'message': _('Not in a meeting.')}), 400

    finalize_idle_period(meeting_idle, datetime.now(pytz.utc))
    return jsonify({'success': True, 'message': 'Meeting mode ended.'})

@api_bp.route('/start_idle_entry/<string:microsoft_oid>', methods=['POST'])
def start_idle_entry(microsoft_oid):
    user = User.query.filter_by(microsoft_oid=microsoft_oid).first_or_404()
    active_entry = TimeEntry.query.filter_by(user_id=user.id, clock_out_time=None).first()
    
    if not active_entry:
        return jsonify({'success': False, 'message': _('User not clocked in.')}), 400

    request_device_id = request.headers.get('X-Device-ID')
    now_utc = datetime.now(pytz.utc).replace(tzinfo=None)

    # --- 1. UPDATE THIS SPECIFIC DEVICE'S STATE FIRST ---
    if request_device_id:
        device_session = UserDeviceSession.query.filter_by(user_id=user.id, device_id=request_device_id).first()
        if device_session:
            device_session.is_idle = True
            device_session.last_heartbeat = now_utc
            db.session.commit()

    # --- 2. MULTI-DEVICE CONSENSUS CHECK ---
    # Are there ANY OTHER devices currently active?
    active_cutoff = now_utc - timedelta(minutes=5)
    other_active_devices = UserDeviceSession.query.filter(
        UserDeviceSession.user_id == user.id,
        UserDeviceSession.device_id != request_device_id,  # Exclude the PC making the request
        UserDeviceSession.is_idle == False,                # Must not be idle
        UserDeviceSession.last_heartbeat >= active_cutoff  # Must have pinged recently
    ).count()

    if other_active_devices > 0:
        log.info(f"Idle request from device {request_device_id} suppressed; Remote activity detected.")
        return jsonify({
            'success': True, 
            'message': 'Remote activity detected. Session remains active.',
            'suppress_idle': True,
            'id': None
        })

    # --- 3. APPLY GLOBAL IDLE (Consensus Met) ---
    existing_idle = IdleEntry.query.filter_by(time_entry_id=active_entry.id, idle_end_time=None).first()
    if existing_idle:
        return jsonify({
            'success': True, 
            'message': _('Already idle.'),
            'id': existing_idle.id
        })

    new_idle = IdleEntry(
        time_entry_id=active_entry.id, 
        idle_start_time=now_utc
    )
    
    db.session.add(new_idle)
    db.session.commit()

    return jsonify({
        'success': True, 
        'message': 'Idle period started.',
        'id': new_idle.id 
    })


@api_bp.route('/end_idle_entry/<int:idle_entry_id>', methods=['POST'])
def end_idle_entry(idle_entry_id):
    idle_entry = IdleEntry.query.get(idle_entry_id)
    if not idle_entry:
        return jsonify({'success': False, 'message': 'Idle entry not found'}), 404

    request_device_id = request.headers.get('X-Device-ID')
    now_utc = datetime.now(pytz.utc).replace(tzinfo=None)

    # --- 1. WAKE UP THIS SPECIFIC DEVICE ---
    # We must explicitly mark this PC as active, otherwise the database
    # will still think it's asleep during the next consensus check!
    if request_device_id:
        user_id = idle_entry.time_entry.user_id
        device_session = UserDeviceSession.query.filter_by(user_id=user_id, device_id=request_device_id).first()
        if device_session:
            device_session.is_idle = False
            device_session.last_heartbeat = now_utc
            db.session.commit()

    # --- 2. END THE GLOBAL IDLE ENTRY ---
    if idle_entry.idle_end_time is None:
        # Assuming finalize_idle_period automatically commits to the DB
        finalize_idle_period(idle_entry, now_utc)

    return jsonify({'success': True, 'message': 'Idle period ended.'})

@api_bp.route('/finalize_idle_reason/<int:idle_entry_id>', methods=['POST'])
def finalize_idle_reason(idle_entry_id):
    idle_entry = IdleEntry.query.get_or_404(idle_entry_id)
    payload = FinalizeIdlePayload.model_validate(request.get_json(force=True))
    finalize_idle_period(idle_entry, datetime.now(pytz.utc), reason=payload.reason)
    return jsonify({'success': True})

@api_bp.route('/dashboard_data_for_electron/<int:user_id>', methods=['GET'])
def dashboard_data_for_electron(user_id):
    user = User.query.get_or_404(user_id)
    _handle_stale_sessions_for_user(user.id)

    now_utc = datetime.now(pytz.utc)
    today_utc_date = now_utc.date()
    config = CompanyConfig.query.first()

    # 1. OPTIMIZED QUERY: Fetch ALL of today's entries with relations in ONE query
    today_entries = TimeEntry.query.options(
        joinedload(TimeEntry.break_entries),
        joinedload(TimeEntry.idle_entries)
    ).filter(
        TimeEntry.user_id == user.id,
        cast(TimeEntry.clock_in_time, Date) == today_utc_date
    ).order_by(TimeEntry.clock_in_time.desc()).all()

    # 2. Find active entry from the loaded list (In Memory)
    active_entry = next((e for e in today_entries if e.clock_out_time is None), None)

    status = get_programmatic_status(user.id, active_entry)
    
    # 3. Get segments (This is the only other query - keeping it safe)
    session_segments = []
    if today_entries:
        session_segments = _get_consolidated_segments_for_user(user.id, today_utc_date, today_utc_date) or []

    # 4. Calculate Totals In Memory (Zero SQL queries)
    total_gross_seconds = 0
    total_break_seconds = 0
    total_idle_seconds = 0

    for e in today_entries:
        # Gross
        end_time = e.clock_out_time or now_utc.replace(tzinfo=None)
        total_gross_seconds += (end_time - e.clock_in_time).total_seconds()

        # Breaks
        for b in e.break_entries:
            b_end = b.break_end_time or now_utc.replace(tzinfo=None)
            total_break_seconds += (b_end - b.break_start_time).total_seconds()
        
        # Idles (Non-Meeting)
        for i in e.idle_entries:
            if i.reason != "In Meeting":
                i_end = i.idle_end_time or now_utc.replace(tzinfo=None)
                total_idle_seconds += (i_end - i.idle_start_time).total_seconds()

    daily_summary = {
        'date': today_utc_date.strftime('%Y-%m-%d'),
        'total_break_seconds': total_break_seconds,
        'total_idle_seconds': total_idle_seconds,
        'net_worked_hours': round(max(0, total_gross_seconds - total_break_seconds - total_idle_seconds) / 3600, 2)
    }

    # 5. Extract active states from the loaded active_entry
    active_break_entry = None
    active_idle_entry = None
    
    if active_entry:
        # Filter in memory instead of new queries
        active_break_entry = next((b for b in active_entry.break_entries if b.break_end_time is None), None)
        active_idle_entry = next((i for i in active_entry.idle_entries if i.idle_end_time is None), None)

    # Automation config
    default_config = { 'max_idle_minutes': 5, 'idle_to_break_minutes': 10, 'long_break_prompt_minutes': 90, 'auto_clock_out_after_break_minutes': 120 }
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

@api_bp.route('/electron/user_activity_segments', methods=['GET'])
def electron_user_activity_segments_api():
    user_id = request.args.get('user_id', type=int)
    start_date_str = request.args.get('start_date')
    if not all([user_id, start_date_str]): return jsonify({'success': False, 'message': 'Missing required parameters.'}), 400
    try:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'success': False, 'message': 'Invalid date format.'}), 400
    
    raw_segments = _get_consolidated_segments_for_user(user_id, start_date, start_date)
    return jsonify({'success': True, 'segments': raw_segments or []})

@api_bp.route('/generate-web-token', methods=['POST'])
def generate_web_token():
    try:
        payload = WebTokenPayload.model_validate(request.get_json(force=True))
    except (ValidationError, json.JSONDecodeError) as e:
        return jsonify({"error": "Invalid input", "details": str(e)}), 400

    token = secrets.token_urlsafe(32)
    sso_tokens[token] = {"user_id": payload.user_id, "expires_at": int(time_module.time()) + 60} # 1 minute expiry
    return jsonify({"token": token}), 200

@api_bp.route('/admin/enforce_auto_clock_out', methods=['POST'])
@require_auth
def enforce_auto_clock_out():
    """
    A secure endpoint for a scheduled task (e.g., Azure WebJob) to clock out
    any users who are still active after the configured end-of-day time.
    """
    config = CompanyConfig.query.first()
    if not config or not config.working_hours_end:
        return jsonify({'success': False, 'message': 'Working hours end time is not configured.'}), 500

    now_utc = datetime.now(pytz.utc)
    now_berlin = now_utc.astimezone(BERLIN_TZ)

    if now_berlin.time() < config.working_hours_end:
        return jsonify({'success': True, 'message': 'Not past working hours yet. No action taken.'}), 200

    stale_entries = TimeEntry.query.filter(
        TimeEntry.clock_out_time.is_(None)
    ).all()

    clocked_out_users = []
    for entry in stale_entries:
        clock_in_berlin = pytz.utc.localize(entry.clock_in_time).astimezone(BERLIN_TZ)
        if clock_in_berlin.date() < now_berlin.date() or \
           (clock_in_berlin.date() == now_berlin.date() and now_berlin.time() >= config.working_hours_end):
            
            user = User.query.get(entry.user_id)
            if not user: continue

            for b in BreakEntry.query.filter_by(time_entry_id=entry.id, break_end_time=None).all():
                b.break_end_time = now_utc.replace(tzinfo=None)
            for i in IdleEntry.query.filter_by(time_entry_id=entry.id, idle_end_time=None).all():
                finalize_idle_period(i, now_utc, reason=_("Server Auto Clock-Out"))
            
            entry.clock_out_time = now_utc.replace(tzinfo=None)
            entry.status = 'auto_clocked_out'
            entry.total_worked_hours = calculate_worked_hours(entry)
            
            db.session.add(ActivityLog(
                user_id=user.id, time_entry_id=entry.id, timestamp=now_utc.replace(tzinfo=None),
                event_type='clock_out', details=json.dumps({'source': 'server_auto_expiry'})
            ))
            
            send_clock_out_email(user, entry)
            clocked_out_users.append(user.username)

    if clocked_out_users:
        db.session.commit()
        log.info(f"Server auto-clocked-out {len(clocked_out_users)} users: {', '.join(clocked_out_users)}")
        return jsonify({'success': True, 'message': f'Successfully clocked out {len(clocked_out_users)} stale sessions.'}), 200
    
    return jsonify({'success': True, 'message': 'No stale sessions found.'}), 200

@api_bp.route('/record_application_usage', methods=['POST'])
def record_application_usage():
    # 1. Capture Raw JSON first to ensure we get device_id even if Pydantic rejects it
    try:
        json_data = request.get_json(force=True)
    except Exception:
        return jsonify({'success': False, 'message': 'Invalid JSON'}), 400

    # 2. Extract Device ID (Priority: Payload > Header)
    device_id = json_data.get('device_id') or request.headers.get('X-Device-ID', 'unknown_device')

    # 3. Validate Payload using Pydantic
    try:
        payload = ApplicationUsagePayload.model_validate(json_data)
    except ValidationError as e:
        return jsonify({'success': False, 'message': 'Invalid input.', 'errors': e.errors()}), 400

    # 4. Find Active TimeEntry
    # We attribute usage to the current clocked-in session
    time_entry = TimeEntry.query.filter_by(
        user_id=payload.user_id,
        clock_out_time=None
    ).order_by(TimeEntry.clock_in_time.desc()).first()

    # Fallback: Check if it belongs to a recently closed entry (within 5 mins)
    # This handles race conditions where the app logs data right as the user clocks out.
    if not time_entry:
        five_minutes_ago = datetime.now(pytz.utc) - timedelta(minutes=5)
        
        last_closed_entry = TimeEntry.query.filter(
            TimeEntry.user_id == payload.user_id,
            TimeEntry.clock_out_time.isnot(None),
            TimeEntry.clock_out_time >= five_minutes_ago.replace(tzinfo=None)
        ).order_by(TimeEntry.clock_out_time.desc()).first()

        # Check if the usage timestamp falls within that closed entry
        if last_closed_entry:
            start_ts = payload.start_time.replace(tzinfo=None)
            if last_closed_entry.clock_in_time <= start_ts <= last_closed_entry.clock_out_time:
                time_entry = last_closed_entry
                log.info(f"Associating usage data with recently closed TimeEntry ID: {time_entry.id}")

    time_entry_id = time_entry.id if time_entry else None

    # 5. Find Last Usage (Scoped by DEVICE)
    # [CRITICAL FIX] We must filter by device_id. 
    # Otherwise, PC2 usage might try to merge with PC1 usage.
    last_usage = None
    if time_entry_id:
        query = ApplicationUsage.query.filter_by(
            user_id=payload.user_id,
            time_entry_id=time_entry_id
        )
        
        # Only try to merge if we are talking about the same device
        if hasattr(ApplicationUsage, 'device_id'):
            query = query.filter_by(device_id=device_id)
            
        last_usage = query.order_by(ApplicationUsage.start_time.desc()).first()

    # 6. Merge or Create Logic
    # If the app, window, and DEVICE are the same as the last record, just update the end time.
    if (last_usage and 
        last_usage.application_name == payload.application_name and 
        last_usage.window_title == payload.window_title):
        
        last_usage.end_time = payload.end_time.replace(tzinfo=None)
        
        # Recalculate duration
        duration = (last_usage.end_time - last_usage.start_time).total_seconds()
        last_usage.duration_seconds = int(duration)
        
        db.session.add(last_usage)
    else:
        # Create new record
        usage = ApplicationUsage(
            user_id=payload.user_id,
            time_entry_id=time_entry_id,
            application_name=payload.application_name,
            window_title=payload.window_title,
            start_time=payload.start_time.replace(tzinfo=None),
            end_time=payload.end_time.replace(tzinfo=None),
            duration_seconds=payload.duration_seconds
        )
        
        # Add device ID if the model supports it (It should based on previous steps)
        if hasattr(usage, 'device_id'):
            usage.device_id = device_id
            
        db.session.add(usage)

    db.session.commit()
    return jsonify({'success': True, 'message': 'Application usage recorded'})

@api_bp.route('/activity', methods=['POST'])
def record_activity():
    """
    Logs user activity (Active/Idle) per device.
    Updates the UserDeviceSession to ensure 'Last Write' doesn't kill active sessions on other PCs.
    """
    try:
        # 1. Parse & Validate Input
        json_data = request.get_json(force=True)
        payload = ActivityPayload.model_validate(json_data)
    except ValidationError as e:
        return jsonify({'success': False, 'message': 'Invalid input.', 'errors': e.errors()}), 400

    # 2. Check User existence
    user = User.query.get(payload.user_id)
    if not user: 
        return jsonify({'success': False, 'message': _('User not found')}), 404

    # 3. Capture Device ID (Priority: Payload > Header)
    # We try to get it from the Pydantic model first, then the raw JSON, then Headers
    device_id = getattr(payload, 'device_id', None) or \
                json_data.get('device_id') or \
                request.headers.get('X-Device-ID', 'unknown_device')

    # -----------------------------------------------------------------------
    # 4. [CRITICAL FIX] Update Live Device Session State
    # -----------------------------------------------------------------------
    # This ensures that if PC1 says "Idle" but PC2 says "Active", 
    # the system knows the user is still working.
    if device_id and device_id != 'unknown_device':
        session = UserDeviceSession.query.filter_by(
            user_id=user.id,
            device_id=device_id
        ).first()

        if not session:
            # First time this device is seen? Register it.
            session = UserDeviceSession(user_id=user.id, device_id=device_id)
            db.session.add(session)

        # Update Heartbeat
        session.last_heartbeat = datetime.utcnow()

        # Update IDLE State based on event type
        # Maps specific events to the binary Active/Idle state
        active_signals = ['active', 'system_resumed', 'screen_unlocked', 'user_input']
        idle_signals = ['idle', 'system_suspend', 'screen_locked']

        if payload.type in active_signals:
            session.is_idle = False
        elif payload.type in idle_signals:
            session.is_idle = True
        
        # Note: We do NOT commit yet; we wait until the end.

    # -----------------------------------------------------------------------
    # 5. Historical Logging (ActivityLog)
    # -----------------------------------------------------------------------
    # We only add a history log if there is an active time entry (usually),
    # though some systems prefer logging everything.
    active_entry = TimeEntry.query.filter_by(user_id=user.id, clock_out_time=None).first()
    
    if active_entry:
        now_utc = datetime.utcnow()

        # Handle Timestamp format safely
        try:
            timestamp_str = payload.timestamp.isoformat() if payload.timestamp else str(now_utc)
        except Exception:
            timestamp_str = str(now_utc)

        # Prepare JSON details
        details_data = {
            'timestamp': timestamp_str,
            'device_id': device_id,
            'source': 'electron_app',
            'metadata': payload.metadata or {}
        }

        activity_log = ActivityLog(
            user_id=user.id,
            time_entry_id=active_entry.id,
            timestamp=now_utc,
            event_type=payload.type,
            details=json.dumps(details_data) # Store as JSON string
        )
        
        # If your ActivityLog model has a specific 'device_id' column, set it here too:
        # activity_log.device_id = device_id 
        
        db.session.add(activity_log)
        
        # Optional: Console Log for debugging
        if payload.type in ['system_resumed', 'screen_unlocked', 'active']:
             log.info(f"Activity 'ACTIVE' for user {user.email} on device {device_id}")
        elif payload.type in ['system_suspend', 'screen_locked', 'idle']:
             log.info(f"Activity 'IDLE' for user {user.email} on device {device_id}")

    # 6. Commit Transaction
    try:
        db.session.commit()
        return jsonify({'success': True, 'message': f'Activity {payload.type} recorded.'}), 200
    except Exception as e:
        db.session.rollback()
        log.error(f"Database error in record_activity: {e}")
        return jsonify({'success': False, 'message': 'Database error'}), 500

@api_bp.route('/get_activity_segments/<int:user_id>', methods=['GET'])
def get_activity_segments(user_id):
    date_str = request.args.get('date')
    if not date_str:
        return jsonify({'success': False, 'message': 'Date parameter is required.'}), 400
    
    try:
        target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'success': False, 'message': 'Invalid date format. Use YYYY-MM-DD.'}), 400

    segments = _get_consolidated_segments_for_user(user_id, target_date, target_date)
    
    # If a user is clocked in but has no other events, create a default "Active" segment
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

# In api.py, ensure you import UserDeviceSession
# from ..models import User, TimeEntry, UserDeviceSession, ...

@api_bp.route('/heartbeat_ping/<string:microsoft_oid>', methods=['POST'])
def receive_heartbeat_ping(microsoft_oid): 
    # [FIX] Get device_id from the request body (Frontend must send this!)
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

    # [FIX] Update specific Device Session (The "OR" Logic)
    if device_id:
        device_session = UserDeviceSession.query.filter_by(
            user_id=user.id, 
            device_id=device_id
        ).first()

        if not device_session:
            device_session = UserDeviceSession(user_id=user.id, device_id=device_id)
            db.session.add(device_session)
        
        device_session.last_heartbeat = now_utc

    # [FIX] Update Global User Heartbeat 
    # We still update this for legacy compatibility, but it represents the 
    # "latest contact from ANY device".
    user.last_heartbeat_utc = now_utc
    
    db.session.commit()
    return jsonify({"message": "Heartbeat ping received"}), 200


@api_bp.route("/heartbeat", methods=["POST"])
@login_required 
def heartbeat_cleanup_job():
    log.info("Scheduler: Running heartbeat cleanup job...")
    now_utc = datetime.utcnow()
    threshold = now_utc - timedelta(minutes=5) 
    
    # [FIX] Optimization: Eager load relationships to prevent N+1 queries
    active_entries = TimeEntry.query.options(
        joinedload(TimeEntry.user),
        joinedload(TimeEntry.break_entries),
        joinedload(TimeEntry.idle_entries)
    ).filter(TimeEntry.clock_out_time.is_(None)).all()

    breaks_started_count = 0 

    for entry in active_entries:
        user_id = entry.user_id
        
        # 1. Check for Active Meetings
        active_meeting = next((i for i in entry.idle_entries if i.idle_end_time is None and i.reason == "In Meeting"), None)
        if active_meeting:
            continue
        
        # 2. Check for Existing Breaks
        active_break = next((b for b in entry.break_entries if b.break_end_time is None), None)
        if active_break:
            continue

        # --- [CRITICAL FIX] Aggregated Activity Check ---
        
        # A. Client-Side Activity (Usage Logs) - May be delayed/skewed
        last_usage = ApplicationUsage.query.filter_by(user_id=user_id)\
            .order_by(ApplicationUsage.end_time.desc()).first()
        ts_usage = last_usage.end_time or last_usage.start_time if last_usage else None

        last_activity = ActivityLog.query.filter_by(user_id=user_id)\
            .order_by(ActivityLog.timestamp.desc()).first()
        ts_activity = last_activity.timestamp if last_activity else None

        # B. Server-Side Presence (Heartbeats) - The "Keep Alive"
        # Check if ANY device has pinged recently. This fixes the "PC2 ignored" issue.
        # We check the UserDeviceSession table for the latest heartbeat from this user.
        last_device_ping = db.session.query(func.max(UserDeviceSession.last_heartbeat))\
            .filter_by(user_id=user_id).scalar()
        
        # Fallback to the user table if no session records yet
        ts_heartbeat = last_device_ping or entry.user.last_heartbeat_utc

        # C. Determine TRUE last seen (Max of all signals)
        # If PC2 is pinging (Server Time), we treat user as active even if 
        # ApplicationUsage (Client Time) is lagging.
        timestamps = [t for t in [ts_usage, ts_activity, ts_heartbeat] if t is not None]
        
        last_seen = max(timestamps) if timestamps else None

        # ----------------------------------------------------------

        # 3. Decision Logic
        if not last_seen or last_seen < threshold:
            # Create a break
            new_break = BreakEntry(
                time_entry_id=entry.id,
                break_start_time=now_utc,
                reason="System Idle (No Heartbeat)"
            )
            db.session.add(new_break)
            breaks_started_count += 1
            log.info(f"[HEARTBEAT] Putting user {entry.user.username} on break. Last seen: {last_seen}")
    
    if breaks_started_count > 0:
        db.session.commit()

    return jsonify({"message": f"Heartbeat cleanup finished. Breaks started: {breaks_started_count}"}), 200

@api_bp.route('/admin/cleanup_zombie_sessions', methods=['POST'])
@require_auth
def cleanup_zombie_sessions_api(current_admin):
    """
    API endpoint for the NEW Azure WebJob (zombie_cleaner.py).
    This finds and clocks out sessions where the client has crashed
    (i.e., stopped sending heartbeat pings).
    """
    try:
        cleaned_count = find_and_clock_out_zombie_sessions()
        return jsonify({
            "message": "Zombie session cleanup executed.",
            "sessions_cleaned": cleaned_count
        }), 200
    except Exception as e:
        log.error(f"Error during scheduled zombie cleanup job: {str(e)}", exc_info=True)
        return jsonify({"message": "An internal error occurred."}), 500

@api_bp.route('/update_break_reason', methods=['POST'])
def update_break_reason():
    """
    Updates the reason for a specific break entry.
    """
    try:
        data = request.get_json()
        break_id = data.get('break_id')
        reason = data.get('reason')

        if not break_id or not reason:
            return jsonify({'status': 'error', 'message': 'Missing break_id or reason'}), 400

        # 1. Find the break
        break_entry = BreakEntry.query.get(break_id)
        
        if not break_entry:
            return jsonify({'status': 'error', 'message': 'Break entry not found'}), 404
            
        # 2. REMOVE THE SECURITY CHECK THAT USES current_user
        # OLD CRASHING CODE: 
        # if time_entry.user_id != current_user.id: ...
        
        # 3. Update the reason
        break_entry.reason = reason
        db.session.commit()

        # 4. FIX LOGGING (Do not use current_user.username)
        # We fetch the username from the break entry itself
        actual_username = break_entry.time_entry.user.username if break_entry.time_entry and break_entry.time_entry.user else "Unknown"
        
        log.info(f"User {actual_username} updated break {break_id} reason to: {reason}")
        return jsonify({'status': 'success', 'message': 'Reason updated'})

    except Exception as e:
        log.error(f"Error updating break reason: {e}")
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500