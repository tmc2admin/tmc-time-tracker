# flask_app/app/blueprints/web.py

from flask import Blueprint, render_template, redirect, url_for, flash, jsonify, request, current_app
from flask_login import current_user, login_required
from flask_babel import gettext as _
import json
from datetime import datetime
from sqlalchemy import text
from .. import app_logger
# Import database and models
from .. import db
from ..models import CoverageEdit  # Import the new model

# Import Service & Helpers
from ..services.web_service import WebService
from ..decorators import domain_required
from ..helpers import _handle_stale_sessions_for_user

web_bp = Blueprint('web', __name__)

@web_bp.route('/dashboard')
@login_required
@domain_required
def dashboard():
    # Trigger cleanup
    _handle_stale_sessions_for_user(current_user.id)
    
    # Fetch Data via Service
    stats = WebService.get_dashboard_stats(current_user)
    
    # Frontend Assets
    translations_json = json.dumps({
        'hours': _('hours'), 'Active': _('Active'), 'On Break': _('On Break'),
        'Idle': _('Idle'), 'Offline': _('Offline')
    })

    return render_template(
        'dashboard.html',
        current_date=datetime.utcnow().date(),
        
        # Unpack stats dictionary
        active_entry=stats['active_entry'],
        active_break=stats['active_break'],
        total_break_seconds=stats['total_break_seconds'],
        total_idle_duration_seconds=stats['total_idle_seconds'],
        
        recent_ot=stats['recent_ot'],
        recent_leaves=stats['recent_leaves'],
        upcoming_holidays=stats['upcoming_holidays'],
        
        expected_clock_out=stats['expected_clock_out'],
        gross_duration_seconds=stats['gross_duration_seconds'],
        user_display_status=stats['user_display_status'],
        
        leave_days_taken=stats['leave_days_taken'],
        leave_days_remaining=stats['leave_days_remaining'],
        
        # User defaults
        default_shift_start=current_user.session_start_time,
        default_hours=getattr(current_user, 'default_daily_hours', 8.0),
        translations=translations_json
    )

@web_bp.route('/fetch_daily_summary')
@login_required
def fetch_daily_summary():
    try:
        s_date = datetime.strptime(request.args.get('start_date'), '%Y-%m-%d').date()
        e_date = datetime.strptime(request.args.get('end_date'), '%Y-%m-%d').date()
        
        data = WebService.get_daily_summary(current_user.id, s_date, e_date)
        # Add date strings for frontend compatibility
        data['start_date'] = s_date.strftime('%d.%m.%Y')
        data['end_date'] = e_date.strftime('%d.%m.%Y')
        return jsonify(data)
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid or missing date format. Use YYYY-MM-DD.'}), 400

@web_bp.route('/fetch_historical_entries')
@login_required
def fetch_historical_entries():
    try:
        s_str = request.args.get('start_date')
        e_str = request.args.get('end_date')
        today = datetime.utcnow().date()
        s_date = datetime.strptime(s_str, '%Y-%m-%d').date() if s_str else today
        e_date = datetime.strptime(e_str, '%Y-%m-%d').date() if e_str else today
        
        return jsonify(WebService.get_historical_entries_consolidated(current_user.id, s_date, e_date))
    except ValueError:
        return jsonify([])

@web_bp.route('/fetch_dashboard_data')
@login_required
def fetch_dashboard_data():
    return jsonify(WebService.get_live_dashboard_data(current_user))

@web_bp.route('/request_overtime', methods=['POST'])
@login_required
def request_overtime():
    try:
        msg = WebService.submit_overtime_request(current_user, request.get_json())
        return jsonify({'success': True, 'message': msg})
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    except Exception as e:
        print(f"Error in request_overtime: {e}")
        return jsonify({'success': False, 'message': _('An internal error occurred.')}), 500

@web_bp.route('/fetch_daily_time_report')
@login_required
def fetch_daily_time_report():
    try:
        s_date = datetime.strptime(request.args.get('start_date'), '%Y-%m-%d').date()
        e_date = datetime.strptime(request.args.get('end_date'), '%Y-%m-%d').date()
        return jsonify(WebService.get_daily_time_report_list(current_user.id, s_date, e_date))
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid dates.'}), 400

@web_bp.route('/request_leave', methods=['POST'])
@login_required
def request_leave():
    try:
        msg = WebService.submit_leave_request(current_user, request.form)
        flash(msg, 'success')
    except ValueError as e:
        flash(str(e), 'danger')
    except Exception as e:
        flash(_('Error submitting request: %(error)s', error=str(e)), 'danger')
        
    return redirect(url_for('web.dashboard'))


@web_bp.route('/klsid-coverage', methods=['GET'])
@login_required
def klsid_coverage():
    """
    Display KLSID Coverage Report data using the customers.html template
    """
    import traceback
    
    # Get filter parameters
    selected_klsid = request.args.get('klsid', '')
    selected_customer = request.args.get('customer', '')
    
    app_logger.info(f"KLSID Coverage page accessed - filters: klsid='{selected_klsid}', customer='{selected_customer}'")
    
    # Check if second database is configured
    if 'klsid_db' not in current_app.config.get('SQLALCHEMY_BINDS', {}):
        app_logger.error("KLSID database not configured in SQLALCHEMY_BINDS")
        flash(_('KLSID database not configured. Please check your environment variables.'), 'danger')
        return render_template(
            'admin/customers.html',
            records=[],
            klsid_list=[],
            customer_list=[],
            selected_klsid='',
            selected_customer='',
            is_klsid_view=True
        )
    
    # Get external database engine
    try:
        from sqlalchemy import create_engine
        bind_uri = current_app.config['SQLALCHEMY_BINDS']['klsid_db']
        external_engine = create_engine(bind_uri)
        app_logger.info("Successfully created engine for klsid_db using create_engine")
        
        # Test the connection
        with external_engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            app_logger.info("Successfully connected to KLSID database")
            
    except Exception as e:
        app_logger.error(f"Could not connect to KLSID database: {str(e)}")
        app_logger.error(traceback.format_exc())
        flash(_('Could not connect to KLSID database: %(error)s', error=str(e)), 'danger')
        return render_template(
            'admin/customers.html',
            records=[],
            klsid_list=[],
            customer_list=[],
            selected_klsid='',
            selected_customer='',
            is_klsid_view=True
        )
    
    try:
        # Get distinct values for dropdowns
        with external_engine.connect() as connection:
            app_logger.info("Fetching distinct KLSIDs...")
            
            # First, check if table exists and has data
            check_query = text("SELECT COUNT(*) as count FROM [dbo].[PBI_KLSID_Coverage_Report] WHERE Account_Name IS NOT NULL")
            count_result = connection.execute(check_query).first()
            app_logger.info(f"Total records with Account_Name IS NOT NULL: {count_result[0] if count_result else 0}")
            
            # Get KLSIDs
            klsid_query = text("SELECT DISTINCT KLSID FROM [dbo].[PBI_KLSID_Coverage_Report] WHERE Account_Name IS NOT NULL AND KLSID IS NOT NULL ORDER BY KLSID")
            klsid_result = connection.execute(klsid_query)
            klsid_list = [row[0] for row in klsid_result if row[0] is not None]
            app_logger.info(f"Found {len(klsid_list)} distinct KLSIDs")
            
            # Get Customer Names
            customer_query = text("SELECT DISTINCT Customer_Name FROM [dbo].[PBI_KLSID_Coverage_Report] WHERE Account_Name IS NOT NULL AND Customer_Name IS NOT NULL ORDER BY Customer_Name")
            customer_result = connection.execute(customer_query)
            customer_list = [row[0] for row in customer_result if row[0] is not None]
            app_logger.info(f"Found {len(customer_list)} distinct Customer Names")
        
        # Build main query
        query = """
            SELECT 
                [KLSID], [Installed_Base_ID], [SalesOrder_Territory], [Account_ID],
                [Customer_Name], [Main_Contact], [Customer_State], [Customer_Address],
                [Account_Manager], [Customer_Status], [Account_ChangedOn], [PLZ],
                [Ort], [Straße], [HausNr], [WE_Liegenschaft], [GE_Liegenschaft],
                [FTTH_Gebiet_Name], [FTTH_Ausbaustatus], [Ausbauprogramm],
                [InstalledBase_CreatedDate], [KLSID_Product]
            FROM [dbo].[PBI_KLSID_Coverage_Report]
            WHERE Account_Name IS NOT NULL
        """
        
        params = {}
        if selected_klsid:
            query += " AND KLSID = :klsid"
            params['klsid'] = selected_klsid
        if selected_customer:
            query += " AND Customer_Name = :customer"
            params['customer'] = selected_customer
        
        query += " ORDER BY KLSID"
        
        app_logger.info(f"Executing main query with params: {params}")
        
        # Execute main query
        with external_engine.connect() as connection:
            result = connection.execute(text(query), params)
            records = []
            row_count = 0
            for row in result:
                row_count += 1
                record = dict(row._mapping)
                # Convert None values to empty strings for template
                for key, value in record.items():
                    if value is None:
                        record[key] = ''
                records.append(record)
            
            app_logger.info(f"Main query returned {row_count} records")
        
        # Merge with local edits - WITH BATCHING FIX FOR LARGE DATASETS
        if records:
            try:
                from ..models import CoverageEdit
                klsids = [r['KLSID'] for r in records if r['KLSID']]
                if klsids:
                    app_logger.info(f"Fetching local edits for {len(klsids)} KLSIDs")
                    
                    # SQL Server has a parameter limit, so we need to batch the query
                    # Process in batches of 1000
                    batch_size = 1000
                    local_edits = {}
                    
                    for i in range(0, len(klsids), batch_size):
                        batch = klsids[i:i+batch_size]
                        app_logger.info(f"Processing batch {i//batch_size + 1}: {len(batch)} KLSIDs")
                        
                        batch_edits = CoverageEdit.query.filter(CoverageEdit.klsid.in_(batch)).all()
                        for edit in batch_edits:
                            local_edits[edit.klsid] = edit
                        
                        app_logger.info(f"Found {len(batch_edits)} edits in this batch")
                    
                    app_logger.info(f"Total local edits found: {len(local_edits)}")
                    
                    # Now merge with records
                    for record in records:
                        k = record['KLSID']
                        if k and k in local_edits:
                            edit = local_edits[k]
                            record['StillInStock'] = edit.still_in_stock or ''
                            record['NewCustomer'] = edit.new_customer or ''
                            record['Notes'] = edit.notes or ''
                            record['LastUpdated'] = edit.last_updated
                            record['UpdatedBy'] = edit.updated_by or ''
                        else:
                            record['StillInStock'] = ''
                            record['NewCustomer'] = ''
                            record['Notes'] = ''
                            record['LastUpdated'] = None
                            record['UpdatedBy'] = ''
            except Exception as e:
                app_logger.error(f"Error merging local edits: {str(e)}")
                # If there's an error, just set default values
                for record in records:
                    record['StillInStock'] = ''
                    record['NewCustomer'] = ''
                    record['Notes'] = ''
                    record['LastUpdated'] = None
                    record['UpdatedBy'] = ''
        
        app_logger.info(f"Rendering template with {len(records)} records")
        
        return render_template(
            'admin/customers.html',
            records=records,
            klsid_list=klsid_list,
            customer_list=customer_list,
            selected_klsid=selected_klsid,
            selected_customer=selected_customer,
            is_klsid_view=True
        )
        
    except Exception as e:
        app_logger.error(f"Error loading KLSID data: {str(e)}")
        app_logger.error(traceback.format_exc())
        flash(_('Error loading KLSID data: %(error)s', error=str(e)), 'danger')
        return render_template(
            'admin/customers.html',
            records=[],
            klsid_list=[],
            customer_list=[],
            selected_klsid='',
            selected_customer='',
            is_klsid_view=True
        )