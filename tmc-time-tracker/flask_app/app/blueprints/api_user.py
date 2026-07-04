# flask_app/app/blueprints/api_user.py

import json
import logging
import secrets
import time as time_module
from datetime import datetime, time
from sqlalchemy import func, cast, Date  # Added missing imports
import pytz
from flask import Blueprint, request, jsonify, current_app
from flask_babel import gettext as _
from pydantic import ValidationError

from .. import db
from ..models import User, CompanyConfig, OvertimeAllocation
from ..validation_models import SsoLoginPayload, OidPayload, WebTokenPayload
from .auth import sso_tokens

log = logging.getLogger(__name__)
api_user_bp = Blueprint('api_user', __name__)

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
    Handle SSO login from Electron client with device locking.
    
    Expected payload:
        {
            "microsoft_oid": "...",
            "email": "...",
            "username": "...",
            "mac_address": "..."  # Device ID
        }
    """
    try:
        # Get raw json to extract mac_address
        payload_data = request.get_json(force=True)
        payload = SsoLoginPayload.model_validate(payload_data)
        client_device_id = payload_data.get('mac_address')
    except (ValidationError, json.JSONDecodeError) as e:
        return jsonify({
            'success': False, 
            'message': 'Invalid input.', 
            'errors': str(e)
        }), 400

    try:
        ALLOWED_DOMAIN = '@tm-connect.de'
        if not str(payload.email).lower().endswith(ALLOWED_DOMAIN):
            return jsonify({
                'success': False, 
                'message': 'Access denied for domain.'
            }), 403

        # Find or create user
        user = User.query.filter_by(microsoft_oid=payload.microsoft_oid).first()
        if not user:
            user = User.query.filter_by(email=str(payload.email)).first()

        if user:
            log.info(f"Existing user '{user.username}' found. Syncing profile info.")
            user.microsoft_oid = payload.microsoft_oid
            user.username = payload.username
            
            # --- DEVICE LOCK LOGIC ---
            if client_device_id:
                # Admin bypass
                if user.is_admin:
                    log.info(f"Admin '{user.username}' logging in. Bypassing device lock check.")
                else:
                    if not user.device_mac_address:
                        # First login on this device
                        log.info(f"Registering new device ID for user '{user.username}'.")
                        user.device_mac_address = client_device_id
                    elif user.device_mac_address != client_device_id:
                        # Device mismatch
                        log.warning(f"Device mismatch for user '{user.username}'.")
                        return jsonify({
                            'success': False, 
                            'message': 'This account is locked to a different device.'
                        }), 403
            else:
                # No device ID sent
                if user.device_mac_address and not user.is_admin:
                    log.warning(f"User '{user.username}' has registered device but sent no ID.")
                    return jsonify({
                        'success': False, 
                        'message': 'Client app outdated. Please update.'
                    }), 400
                
                log.info(f"No Device ID received for user '{user.username}'. Login allowed.")
        else:
            # Create new user
            log.info(f"Creating new user '{payload.username}'.")
            config = CompanyConfig.query.first()
            user = User(
                username=payload.username,
                email=str(payload.email),
                microsoft_oid=payload.microsoft_oid,
                device_mac_address=client_device_id,
                default_daily_hours=config.default_daily_hours if config else 8.0,
                default_working_days=config.default_working_days if config else 'Monday,Tuesday,Wednesday,Thursday,Friday'
            )
            db.session.add(user)

        db.session.commit()
        return jsonify({
            'success': True, 
            'user_id': user.id, 
            'username': user.username, 
            'email': str(payload.email)
        }), 200

    except Exception as e:
        db.session.rollback()
        log.error(f"SSO login failed: {e}", exc_info=True)
        return jsonify({
            'success': False, 
            'message': f'Database error: {str(e)}'
        }), 500


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