import secrets
import time as time_module
import requests
from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, session
from flask_login import login_user, logout_user, current_user, login_required

from .. import db, oauth
from ..models import User, CompanyConfig

auth_bp = Blueprint('auth', __name__)

sso_tokens = {} # In-memory store for SSO tokens, consider Redis for production

@auth_bp.route('/')
def index():
    if current_user.is_authenticated:
        redirect_url = 'admin.dashboard' if current_user.is_admin else 'web.dashboard'
        return redirect(url_for(redirect_url))
    return render_template('index.html')

@auth_bp.route('/register')
def register():
    flash('Please use your Microsoft account to register or log in.', 'info')
    return redirect(url_for('auth.login_microsoft'))

@auth_bp.route('/login')
def login():
    return redirect(url_for('auth.login_microsoft'))

@auth_bp.route('/login/microsoft')
def login_microsoft():
    redirect_uri = url_for('auth.authorize_microsoft', _external=True)
    return oauth.microsoft.authorize_redirect(redirect_uri, prompt='select_account')

@auth_bp.route('/auth/callback')
def authorize_microsoft():
    try:
        token = oauth.microsoft.authorize_access_token()
        resp = oauth.microsoft.get('https://graph.microsoft.com/v1.0/me', token=token)
        resp.raise_for_status()
        profile = resp.json()
    except Exception as e:
        flash(f'Authentication failed: {e}', 'danger')
        return redirect(url_for('auth.index'))

    microsoft_oid = profile.get('id')
    email = profile.get('mail')
    name = profile.get('displayName') or email

    ALLOWED_DOMAIN = '@tm-connect.de'
    if not email or not email.lower().endswith(ALLOWED_DOMAIN):
        flash(f'Access denied. Only users with a {ALLOWED_DOMAIN} email are allowed.', 'danger')
        return redirect(url_for('auth.index'))

    user = User.query.filter_by(microsoft_oid=microsoft_oid).first()
    if not user and email:
        user = User.query.filter_by(email=email).first()
        if user:
            user.microsoft_oid = microsoft_oid
            db.session.commit()

    if not user:
        config = CompanyConfig.query.first()
        user = User(
            username=name,
            email=email,
            microsoft_oid=microsoft_oid,
            default_daily_hours=config.default_daily_hours if config else 8.0,
            default_working_days=config.default_working_days if config else 'Monday,Tuesday,Wednesday,Thursday,Friday'
        )
        db.session.add(user)
        db.session.commit()

    login_user(user)
    flash('Logged in successfully via Microsoft.', 'success')
    return redirect(url_for('web.dashboard'))

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('auth.index'))

@auth_bp.route('/set_language/<lang_code>')
def set_language(lang_code):
    # This could also live in the 'web' blueprint if preferred
    if lang_code in ['en', 'de']: # Simplified check
        session['lang'] = lang_code
    return redirect(request.referrer or url_for('auth.index'))

@auth_bp.route('/login-with-token')
def login_with_token():
    token = request.args.get('token')
    if not token or token not in sso_tokens:
        return redirect(url_for('auth.login'))

    token_data = sso_tokens.pop(token, None)
    if not token_data or int(time_module.time()) > token_data['expires_at']:
        flash("Login link has expired. Please try again.", "danger")
        return redirect(url_for('auth.login'))

    user = User.query.get(token_data['user_id'])
    if user:
        login_user(user)
        return redirect(url_for('web.dashboard'))
    
    flash("Invalid login attempt.", "danger")
    return redirect(url_for('auth.login'))