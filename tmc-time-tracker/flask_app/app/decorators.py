# decorators.py
from functools import wraps
from flask import redirect, url_for, flash
from flask_login import current_user, logout_user
from flask_babel import gettext as _
from functools import wraps
from flask import request, jsonify, current_app

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash(_('You do not have permission to access this page.'), 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function

def domain_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        ALLOWED_DOMAIN = '@tm-connect.de'
        if not current_user.is_authenticated:
            return redirect(url_for('index'))
        
        if not current_user.email or not current_user.email.lower().endswith(ALLOWED_DOMAIN):
            logout_user()
            flash(_('Access denied. Your account is not from an approved domain.'), 'danger')
            return redirect(url_for('index'))
            
        return f(*args, **kwargs)
    return decorated_function

def require_auth(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth = request.authorization
        if auth and auth.username == "webjob-service" and \
           auth.password == 'testpassword':  
            return f(*args, **kwargs)

        # --- 3. Flask-Login Session (Fallback for Admins using browser) ---
        if current_user.is_authenticated and current_user.is_admin:
            return f(*args, **kwargs)

        # --- Authentication failed ---
        return jsonify({"success": False, "message": "Authentication required."}), 401

    return decorated_function