# flask_app/app/__init__.py

import os
import logging
import pytz
from datetime import datetime, timedelta
from flask import Flask, session, request, render_template, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_babel import Babel
from flask_mail import Mail
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from flask_migrate import Migrate
from werkzeug.middleware.proxy_fix import ProxyFix
from sqlalchemy.exc import OperationalError
from sqlalchemy import func, cast, Date, text

load_dotenv()

# --- Extension Initialization ---
db = SQLAlchemy()
login_manager = LoginManager()
babel = Babel()
mail = Mail()
oauth = OAuth()
migrate = Migrate()

# --- Logging Configuration ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
app_logger = logging.getLogger(__name__)


# --- App Factory ---
def create_app():
    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

    # --- FIX: Enable **kwargs in Jinja macros ---
    app.jinja_env.newstyle = True

    # --- Configuration ---
    secret_key = os.environ.get('SECRET_KEY')
    if not secret_key:
        raise ValueError("FATAL: SECRET_KEY environment variable is not set.")
    
    app.config['SECRET_KEY'] = secret_key
    
    # Main database connection
    main_db_uri = f"mssql+pyodbc://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}@{os.getenv('DB_SERVER')}/{os.getenv('DB_NAME')}?driver=ODBC+Driver+17+for+SQL+Server"
    
    # KLSID database connection (second database)
    klsid_db_uri = None
    if all([os.getenv('KLSID_DB_SERVER'), os.getenv('KLSID_DB_NAME'), 
            os.getenv('KLSID_DB_USER'), os.getenv('KLSID_DB_PASSWORD')]):
        klsid_db_uri = f"mssql+pyodbc://{os.getenv('KLSID_DB_USER')}:{os.getenv('KLSID_DB_PASSWORD')}@{os.getenv('KLSID_DB_SERVER')}/{os.getenv('KLSID_DB_NAME')}?driver=ODBC+Driver+17+for+SQL+Server"
    
    # Base configuration
    config_dict = {
        'SQLALCHEMY_DATABASE_URI': main_db_uri,
        'SQLALCHEMY_TRACK_MODIFICATIONS': False,
        'SQLALCHEMY_ENGINE_OPTIONS': {"pool_pre_ping": True, "pool_recycle": 1800},
        'OAUTH_CLIENT_ID': os.getenv('OAUTH_CLIENT_ID'),
        'OAUTH_CLIENT_SECRET': os.getenv('OAUTH_CLIENT_SECRET'),
        'OAUTH_AUTHORITY': os.getenv('OAUTH_AUTHORITY'),
        'OAUTH_REDIRECT_URI': os.getenv('OAUTH_REDIRECT_URI', 'http://localhost:5000/auth/callback'),
        'BABEL_DEFAULT_LOCALE': 'de',
        'BABEL_TRANSLATION_DIRECTORIES': '../translations',
        'LANGUAGES': {'de': 'Deutsch', 'en': 'English'},
        'BERLIN_TZ': pytz.timezone('Europe/Berlin')
    }
    
    # Add binds if second database is configured
    if klsid_db_uri:
        config_dict['SQLALCHEMY_BINDS'] = {
            'klsid_db': klsid_db_uri  # This key is used to reference this database
        }
        app_logger.info("KLSID database configured successfully")
    else:
        app_logger.warning("KLSID database not configured - KLSID features will be disabled")
    
    app.config.update(config_dict)

    # --- Initialize Extensions ---
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    babel.init_app(
        app, 
        locale_selector=lambda: session.get('lang') or request.accept_languages.best_match(app.config['LANGUAGES'].keys())
    )
    mail.init_app(app)
    oauth.init_app(app)

    login_manager.login_view = 'auth.index'

    # --- Microsoft OAuth ---
    oauth.register(
        name='microsoft',
        client_id=app.config['OAUTH_CLIENT_ID'],
        client_secret=app.config['OAUTH_CLIENT_SECRET'],
        server_metadata_url=f"{app.config['OAUTH_AUTHORITY']}/v2.0/.well-known/openid-configuration",
        client_kwargs={
            'scope': 'openid profile email User.Read',
            'code_challenge_method': 'S256'
        },
    )

    # Language change route
    @app.route('/language/<lang>')
    def set_language(lang=None):
        if lang in app.config['LANGUAGES']:
            session['lang'] = lang
        return redirect(request.referrer or url_for('web.dashboard'))

    # --- BLUEPRINT REGISTRATION SECTION ---
    # All blueprints (old and new) are registered here
    
    # 1. Import all blueprints (maintain alphabetical order for clarity)
    from .blueprints.admin import admin_bp
    from .blueprints.api_activity import api_activity_bp
    from .blueprints.api_breaks import api_breaks_bp
    from .blueprints.api_time import api_time_bp
    from .blueprints.api_user import api_user_bp
    from .blueprints.api_coverage import api_coverage_bp
    from .blueprints.auth import auth_bp
    from .blueprints.web import web_bp

    # 2. Register web and auth blueprints (no URL prefix)
    app.register_blueprint(auth_bp)          # Routes: /, /login, /logout, /auth/callback, etc.
    app.register_blueprint(web_bp)            # Routes: /dashboard, /fetch_*, /request_*, etc.

    # 3. Register admin blueprint with /admin prefix
    app.register_blueprint(admin_bp, url_prefix='/admin')
    # Admin routes: /admin/dashboard, /admin/settings, /admin/api/*, etc.

    # 4. Register all API blueprints with /api prefix
    app.register_blueprint(api_user_bp, url_prefix='/api')        # User management routes
    app.register_blueprint(api_time_bp, url_prefix='/api')        # Time entry routes
    app.register_blueprint(api_breaks_bp, url_prefix='/api')      # Break and meeting routes
    app.register_blueprint(api_activity_bp, url_prefix='/api')    # Activity and heartbeat routes
    app.register_blueprint(api_coverage_bp, url_prefix='/api')    # KLSID coverage routes

    # --- CLI Commands ---
    from .commands import seed_command
    app.cli.add_command(seed_command)

    # Log all registered routes for debugging (only in development)
    if app.debug:
        app_logger.info("Registered Routes:")
        for rule in app.url_map.iter_rules():
            app_logger.info(f"  {rule.endpoint}: {rule.methods} {rule.rule}")

    # --- Models & Login Manager ---
    from . import models

    @login_manager.user_loader
    def load_user(user_id):
        return models.User.query.get(int(user_id))

    @login_manager.unauthorized_handler
    def unauthorized_callback():
        return redirect(url_for('auth.index'))

    # --- Jinja Filters & Context ---
    from .helpers import utc_to_berlin_filter, format_duration_filter, calculate_worked_hours
    
    app.jinja_env.filters['utc_to_berlin'] = utc_to_berlin_filter
    app.jinja_env.filters['format_duration'] = format_duration_filter

    @app.context_processor
    def inject_globals():
        from flask_babel import get_locale
        return {
            'current_year': datetime.now().year,
            'current_locale': get_locale(),
            'languages': app.config['LANGUAGES']
        }

    # --- Error Handlers ---
    @app.errorhandler(OperationalError)
    def handle_db_connection_error(error):
        """
        Catches database connection errors (e.g., firewall block, timeout).
        Logs the error and shows a user-friendly "service unavailable" page.
        """
        app_logger.error(f"Database connection error: {error}")
        return render_template('errors/503.html'), 503

    @app.errorhandler(404)
    def not_found_error(error):
        return render_template('errors/404.html'), 404

    @app.errorhandler(500)
    def internal_error(error):
        try:
            db.session.rollback()
        except:
            pass  # Ignore DB errors during error handling
        return render_template('errors/500.html'), 500

    return app