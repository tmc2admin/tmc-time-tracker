# flask_app/app/blueprints/api_breaks.py

import json
import logging
from datetime import datetime, timedelta
from sqlalchemy import func, cast, Date  # Added missing imports
import pytz
from flask import Blueprint, request, jsonify, current_app
from flask_babel import gettext as _
from pydantic import ValidationError

from .. import db
from ..models import User, TimeEntry, BreakEntry, IdleEntry, CompanyConfig, UserDeviceSession, ActivityLog
from ..helpers import finalize_idle_period
from ..validation_models import BreakReasonPayload

log = logging.getLogger(__name__)
api_breaks_bp = Blueprint('api_breaks', __name__)

# --- Timezone Configuration - FIXED: Don't access current_app at module level ---
def get_berlin_tz():
    """Get Berlin timezone from app config or return default."""
    try:
        return current_app.config.get('BERLIN_TZ', pytz.timezone('Europe/Berlin'))
    except RuntimeError:
        # Outside application context, return default
        return pytz.timezone('Europe/Berlin')


@api_breaks_bp.route('/start_break_for_electron/<string:microsoft_oid>', methods=['POST'])
def start_break_for_electron(microsoft_oid):
    """
    Start a break for a user.
    
    Path parameters:
        microsoft_oid: Microsoft OID of the user
    
    This endpoint uses multi-device consensus - if another device is active,
    the break request may be suppressed.
    """
    user = User.query.filter_by(microsoft_oid=microsoft_oid).first_or_404()
    active_entry = TimeEntry.query.filter_by(user_id=user.id, clock_out_time=None).first()
    
    if not active_entry:
        return jsonify({'success': False, 'message': _('Not clocked in.')}), 400

    request_device_id = request.headers.get('X-Device-ID')
    now_utc = datetime.now(pytz.utc).replace(tzinfo=None)

    # --- 1. UPDATE THIS SPECIFIC DEVICE'S STATE ---
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
        log.info(f"Break request from device {request_device_id} suppressed; User active elsewhere.")
        return jsonify({
            'success': True,
            'message': 'Remote activity detected. Global break suppressed.',
            'suppress_break': True
        }), 200

    # --- 3. APPLY GLOBAL BREAK (Consensus Met) ---
    if BreakEntry.query.filter_by(time_entry_id=active_entry.id, break_end_time=None).first():
        return jsonify({'success': False, 'message': _('Already on a break.')}), 400

    # Finalize any open idle periods
    for i in IdleEntry.query.filter_by(time_entry_id=active_entry.id, idle_end_time=None).all():
        finalize_idle_period(i, now_utc, reason=_("Break started"))

    new_break = BreakEntry(time_entry_id=active_entry.id, break_start_time=now_utc)
    db.session.add(new_break)
    db.session.commit()
    
    return jsonify({'success': True, 'message': _('Break started.')}), 200


@api_breaks_bp.route('/end_break_for_electron/<string:microsoft_oid>', methods=['POST'])
def end_break_for_electron(microsoft_oid):
    """
    End a break for a user.
    
    Path parameters:
        microsoft_oid: Microsoft OID of the user
    
    Returns:
        JSON with prompt_for_reason and break_id if break exceeded long break threshold.
    """
    user = User.query.filter_by(microsoft_oid=microsoft_oid).first_or_404()
    active_entry = TimeEntry.query.filter_by(user_id=user.id, clock_out_time=None).first()
    
    if not active_entry:
        return jsonify({'success': False, 'message': _('Not clocked in.')}), 400

    request_device_id = request.headers.get('X-Device-ID')
    now_utc = datetime.now(pytz.utc).replace(tzinfo=None)

    # --- 1. WAKE UP THIS SPECIFIC DEVICE ---
    if request_device_id:
        device_session = UserDeviceSession.query.filter_by(
            user_id=user.id, device_id=request_device_id
        ).first()
        if device_session:
            device_session.is_idle = False
            device_session.last_heartbeat = now_utc
            db.session.commit()
    
    # --- 2. END THE GLOBAL BREAK ---
    open_breaks = BreakEntry.query.filter_by(
        time_entry_id=active_entry.id, break_end_time=None
    ).all()
    
    if not open_breaks:
        return jsonify({'success': True, 'message': _('No active breaks found.')}), 200
    
    # Track the most recent break for prompt
    primary_break = open_breaks[0]
    longest_duration_sec = 0
    
    for b in open_breaks:
        b.break_end_time = now_utc
        duration = (b.break_end_time - b.break_start_time).total_seconds()
        if duration > longest_duration_sec:
            longest_duration_sec = duration
            primary_break = b

    db.session.commit()

    # --- 3. Check if prompt needed for long break ---
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


# --- UNIFIED BREAK REASON HANDLER ---
def _process_break_reason(break_id, reason):
    """
    Internal unified handler for break reason updates.
    
    Args:
        break_id: ID of the break entry
        reason: Reason text
    
    Returns:
        Tuple (success, message, status_code)
    """
    try:
        break_entry = BreakEntry.query.get(break_id)
        if not break_entry:
            return False, 'Break entry not found', 404

        break_entry.reason = reason
        
        # Log the update
        actual_username = break_entry.time_entry.user.username if break_entry.time_entry and break_entry.time_entry.user else "Unknown"
        log.info(f"User {actual_username} updated break {break_id} reason to: {reason}")
        
        db.session.commit()
        return True, 'Reason updated', 200
        
    except Exception as e:
        db.session.rollback()
        log.error(f"Error updating break reason: {e}")
        return False, str(e), 500


@api_breaks_bp.route('/submit_break_reason/<int:break_id>', methods=['POST'])
def submit_break_reason(break_id):
    """
    Submit a reason for a break (Pydantic validated).
    
    Path parameters:
        break_id: ID of the break entry
    
    Expected payload:
        {
            "reason": "Lunch break"
        }
    """
    try:
        payload = BreakReasonPayload.model_validate(request.get_json(force=True))
        success, message, status_code = _process_break_reason(break_id, payload.reason)
        
        if success:
            return jsonify({'success': True, 'message': message}), status_code
        else:
            return jsonify({'success': False, 'message': message}), status_code
            
    except ValidationError as e:
        return jsonify({'success': False, 'message': 'Invalid input', 'errors': e.errors()}), 400


@api_breaks_bp.route('/update_break_reason', methods=['POST'])
def update_break_reason():
    """
    Legacy endpoint for break reason updates.
    
    Expected payload:
        {
            "break_id": 123,
            "reason": "Lunch break"
        }
    """
    try:
        data = request.get_json()
        break_id = data.get('break_id')
        reason = data.get('reason')

        if not break_id or not reason:
            return jsonify({'status': 'error', 'message': 'Missing break_id or reason'}), 400

        success, message, status_code = _process_break_reason(break_id, reason)
        
        if success:
            return jsonify({'status': 'success', 'message': message}), status_code
        else:
            return jsonify({'status': 'error', 'message': message}), status_code

    except Exception as e:
        log.error(f"Error in update_break_reason: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@api_breaks_bp.route('/start_meeting_for_electron/<string:microsoft_oid>', methods=['POST'])
def start_meeting_for_electron(microsoft_oid):
    """
    Start meeting mode (special idle type).
    
    Path parameters:
        microsoft_oid: Microsoft OID of the user
    """
    user = User.query.filter_by(microsoft_oid=microsoft_oid).first_or_404()
    active_entry = TimeEntry.query.filter_by(user_id=user.id, clock_out_time=None).first()
    
    if not active_entry:
        return jsonify({'success': False, 'message': _('Not clocked in')}), 400
    
    # Check if already in a meeting
    existing_meeting = IdleEntry.query.filter_by(
        time_entry_id=active_entry.id,
        idle_end_time=None,
        reason="In Meeting"
    ).first()
    
    if existing_meeting:
        return jsonify({'success': True, 'message': 'Meeting mode is already active.'}), 200

    now_utc = datetime.now(pytz.utc)
    
    # End any ongoing breaks or idle periods
    for b in BreakEntry.query.filter_by(time_entry_id=active_entry.id, break_end_time=None).all():
        b.break_end_time = now_utc.replace(tzinfo=None)
    for i in IdleEntry.query.filter_by(time_entry_id=active_entry.id, idle_end_time=None).all():
        finalize_idle_period(i, now_utc, reason=_("Meeting started"))

    # Create meeting entry
    db.session.add(IdleEntry(
        time_entry_id=active_entry.id,
        idle_start_time=now_utc.replace(tzinfo=None),
        reason="In Meeting"
    ))
    db.session.commit()
    
    return jsonify({'success': True, 'message': 'Meeting mode started.'})


@api_breaks_bp.route('/end_meeting_for_electron/<string:microsoft_oid>', methods=['POST'])
def end_meeting_for_electron(microsoft_oid):
    """
    End meeting mode.
    
    Path parameters:
        microsoft_oid: Microsoft OID of the user
    """
    user = User.query.filter_by(microsoft_oid=microsoft_oid).first_or_404()
    active_entry = TimeEntry.query.filter_by(user_id=user.id, clock_out_time=None).first()
    
    if not active_entry:
        return jsonify({'success': False, 'message': _('Not clocked in')}), 400

    meeting_idle = IdleEntry.query.filter_by(
        time_entry_id=active_entry.id,
        idle_end_time=None,
        reason="In Meeting"
    ).order_by(IdleEntry.idle_start_time.desc()).first()
    
    if not meeting_idle:
        return jsonify({'success': False, 'message': _('Not in a meeting.')}), 400

    finalize_idle_period(meeting_idle, datetime.now(pytz.utc))
    return jsonify({'success': True, 'message': 'Meeting mode ended.'})