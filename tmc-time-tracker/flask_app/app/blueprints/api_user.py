# flask_app/app/blueprints/api_user.py

import json
import logging
import secrets
import time as time_module
from datetime import datetime, time, timedelta
from sqlalchemy import func, cast, Date  # Added missing imports
import pytz
from flask import Blueprint, request, jsonify, current_app
from flask_babel import gettext as _
from pydantic import ValidationError

from .. import db
from ..models import User, CompanyConfig, OvertimeAllocation, UserDeviceSession
from ..validation_models import SsoLoginPayload, OidPayload, WebTokenPayload
from .auth import sso_tokens

log = logging.getLogger(__name__)
api_user_bp = Blueprint('api_user', __name__)
ACTIVE_DEVICE_TIMEOUT = timedelta(minutes=3)

# --- Timezone Configuration - FIXED: Don't access current_app at module level ---
def get_berlin_tz():
    """Get Berlin timezone from app config or return default."""
    try:
        return current_app.config.get('BERLIN_TZ', pytz.timezone('Europe/Berlin'))
    except RuntimeError:
        # Outside application context, return default
        return pytz.timezone('Europe/Berlin')


@api_user_bp.route('/v1/config/<int:user_id>', methods=['GET'])
def get_user_config(user_id):
    """
    Get user configuration including working hours and automation settings.
    
    Returns:
        JSON with startTime, endTime, isSuspended, serverTime, etc.
    """
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
                    effective_start_time = datetime.strptime(
                        raw_start.split('.')[0], "%H:%M:%S"
                    ).time()
                elif isinstance(raw_start, datetime):
                    effective_start_time = raw_start.time()
                else:
                    effective_start_time = raw_start

            # ---------- END TIME ----------
            raw_end = config.working_hours_end
            if raw_end:
                if isinstance(raw_end, str):
                    effective_end_time = datetime.strptime(
                        raw_end.split('.')[0], "%H:%M:%S"
                    ).time()
                elif isinstance(raw_end, datetime):
                    effective_end_time = raw_end.time()
                else:
                    effective_end_time = raw_end

        # 3. Handle Timezones
        now_utc = datetime.now(pytz.utc)
        berlin_tz = get_berlin_tz()
        now_berlin = now_utc.astimezone(berlin_tz)
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
                    raw_ot_end.split('.')[0], "%H:%M:%S"
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
        log.error(f"ERROR in get_user_config: {str(e)}", exc_info=True)
        return jsonify({
            'startTime': '09:00:00',
            'endTime': '17:00:00',
            'isSuspended': False,
            'serverTime': datetime.utcnow().isoformat() + 'Z'
        }), 200


@api_user_bp.route('/electron_sso_login', methods=['POST'])
def electron_sso_login():
    """
    Handle SSO login from Electron and coordinate the active desktop device.
    
    Expected payload:
        {
            "microsoft_oid": "...",
            "email": "...",
            "username": "...",
            "mac_address": "...",  # Device ID
            "device_name": "...",
            "confirm_takeover": false
        }
    """
    try:
        payload_data = request.get_json(force=True)
        payload = SsoLoginPayload.model_validate(payload_data)
        client_device_id = str(
            payload_data.get('mac_address') or payload_data.get('device_id') or ''
        ).strip()
        device_name = str(payload_data.get('device_name') or '').strip()[:100] or None
        confirm_takeover = payload_data.get('confirm_takeover') is True
    except (ValidationError, json.JSONDecodeError) as e:
        return jsonify({
            'success': False, 
            'message': 'Invalid input.', 
            'errors': str(e)
        }), 400

    if not client_device_id:
        return jsonify({
            'success': False,
            'code': 'E_DEVICE_REQUIRED',
            'message': 'A device ID is required. Please update the desktop app.'
        }), 400

    try:
        ALLOWED_DOMAIN = '@tm-connect.de'
        if not str(payload.email).lower().endswith(ALLOWED_DOMAIN):
            return jsonify({
                'success': False, 
                'message': 'Access denied for domain.'
            }), 403

        now_utc = datetime.now(pytz.utc).replace(tzinfo=None)
        user = User.query.filter_by(microsoft_oid=payload.microsoft_oid).first()
        if not user:
            user = User.query.filter_by(email=str(payload.email)).first()

        if user:
            previous_device_id = user.device_mac_address
            previous_session = None
            previous_device_active = False

            if previous_device_id and previous_device_id != client_device_id:
                previous_session = UserDeviceSession.query.filter_by(
                    user_id=user.id,
                    device_id=previous_device_id
                ).first()
                previous_device_active = bool(
                    previous_session
                    and previous_session.last_heartbeat
                    and previous_session.last_heartbeat >= now_utc - ACTIVE_DEVICE_TIMEOUT
                )

                if previous_device_active and not confirm_takeover:
                    return jsonify({
                        'success': False,
                        'code': 'E_ACTIVE_SESSION',
                        'message': 'This account is active on another PC.',
                        'previous_device_name': previous_session.device_name
                    }), 409

                if previous_session:
                    previous_session.is_idle = True
                    previous_session.last_heartbeat = (
                        now_utc - ACTIVE_DEVICE_TIMEOUT - timedelta(seconds=1)
                    )

            log.info(f"Activating device '{client_device_id}' for user '{user.username}'.")
            user.microsoft_oid = payload.microsoft_oid
            user.username = payload.username
        else:
            log.info(f"Creating new user '{payload.username}'.")
            config = CompanyConfig.query.first()
            user = User(
                username=payload.username,
                email=str(payload.email),
                microsoft_oid=payload.microsoft_oid,
                device_mac_address=None,
                default_daily_hours=config.default_daily_hours if config else 8.0,
                default_working_days=config.default_working_days if config else 'Monday,Tuesday,Wednesday,Thursday,Friday'
            )
            db.session.add(user)
            db.session.flush()
            previous_device_id = None
            previous_device_active = False

        # A physical PC can only host one active desktop account at a time.
        other_users = User.query.filter(
            User.device_mac_address == client_device_id,
            User.id != user.id
        ).all()
        for other_user in other_users:
            other_user.device_mac_address = None
            other_session = UserDeviceSession.query.filter_by(
                user_id=other_user.id,
                device_id=client_device_id
            ).first()
            if other_session:
                other_session.is_idle = True
                other_session.last_heartbeat = (
                    now_utc - ACTIVE_DEVICE_TIMEOUT - timedelta(seconds=1)
                )

        if other_users:
            db.session.flush()

        user.device_mac_address = client_device_id
        user.last_heartbeat_utc = now_utc

        device_session = UserDeviceSession.query.filter_by(
            user_id=user.id,
            device_id=client_device_id
        ).first()
        if not device_session:
            device_session = UserDeviceSession(
                user_id=user.id,
                device_id=client_device_id
            )
            db.session.add(device_session)

        device_session.device_name = device_name
        device_session.ip_address = request.remote_addr
        device_session.is_idle = False
        device_session.last_heartbeat = now_utc

        db.session.commit()
        return jsonify({
            'success': True, 
            'user_id': user.id, 
            'username': user.username, 
            'email': str(payload.email),
            'device_id': client_device_id,
            'session_taken_over': bool(
                previous_device_id
                and previous_device_id != client_device_id
                and previous_device_active
            )
        }), 200

    except Exception as e:
        db.session.rollback()
        log.error(f"SSO login failed: {e}", exc_info=True)
        return jsonify({
            'success': False, 
            'message': f'Database error: {str(e)}'
        }), 500


@api_user_bp.route('/electron_logout', methods=['POST'])
def electron_logout():
    """Release the current desktop device without changing time entries."""
    payload_data = request.get_json(silent=True) or {}
    microsoft_oid = str(payload_data.get('microsoft_oid') or '').strip()
    client_device_id = str(
        payload_data.get('device_id')
        or payload_data.get('mac_address')
        or request.headers.get('X-Device-ID')
        or ''
    ).strip()

    if not microsoft_oid or not client_device_id:
        return jsonify({
            'success': False,
            'message': 'Microsoft user ID and device ID are required.'
        }), 400

    user = User.query.filter_by(microsoft_oid=microsoft_oid).first()
    if not user:
        return jsonify({'success': True}), 200

    now_utc = datetime.now(pytz.utc).replace(tzinfo=None)
    if user.device_mac_address == client_device_id:
        user.device_mac_address = None

    device_session = UserDeviceSession.query.filter_by(
        user_id=user.id,
        device_id=client_device_id
    ).first()
    if device_session:
        device_session.is_idle = True
        device_session.last_heartbeat = (
            now_utc - ACTIVE_DEVICE_TIMEOUT - timedelta(seconds=1)
        )

    db.session.commit()
    return jsonify({'success': True}), 200


@api_user_bp.route('/get_user_id_from_oid', methods=['POST'])
def get_user_id_from_oid():
    """
    Get user ID from Microsoft OID.
    
    Expected payload:
        {
            "microsoft_oid": "..."
        }
    """
    try:
        payload = OidPayload.model_validate(request.get_json(force=True))
    except (ValidationError, json.JSONDecodeError) as e:
        return jsonify({'error': 'Invalid input.', 'details': str(e)}), 400

    user = User.query.filter_by(microsoft_oid=payload.microsoft_oid).first()
    if user:
        return jsonify({'user_id': user.id}), 200
    return jsonify({'error': 'User not found'}), 404


@api_user_bp.route('/generate-web-token', methods=['POST'])
def generate_web_token():
    """
    Generate a one-time token for web dashboard access from Electron.
    
    Expected payload:
        {
            "user_id": 123
        }
    """
    try:
        payload = WebTokenPayload.model_validate(request.get_json(force=True))
    except (ValidationError, json.JSONDecodeError) as e:
        return jsonify({"error": "Invalid input", "details": str(e)}), 400

    token = secrets.token_urlsafe(32)
    sso_tokens[token] = {
        "user_id": payload.user_id, 
        "expires_at": int(time_module.time()) + 60  # 1 minute expiry
    }
    return jsonify({"token": token}), 200
