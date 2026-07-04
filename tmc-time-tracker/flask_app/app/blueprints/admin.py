# blueprints/admin.py
from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, Response
from flask_login import login_required, current_user
from functools import wraps
import os
import logging
from datetime import datetime, date, timedelta

# Import Services & Helpers
from ..services.admin_service import AdminService
from ..models import (db, User, CompanyConfig, Holiday, Provision, LeaveRequest, Customer, TelekomPassword, OvertimeAllocation)
from ..forms import ReportForm, UserEditForm, CompanyConfigForm, AdminReportsForm, HolidayForm, ProvisionForm
from ..validation_models import AdminReportRequestArgs, AdminHistoricalTimelineArgs
from ..helpers import _calculate_report_metrics_for_period, utc_to_berlin_filter
from ..decorators import admin_required
from flask_babel import gettext as _
from pydantic import ValidationError

admin_bp = Blueprint('admin', __name__, template_folder='../templates/admin')

# --- AUTH DECORATOR ---
def require_webjob_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        webjob_user = os.getenv('WEBJOB_USERNAME')
        webjob_pass = os.getenv('WEBJOB_PASSWORD')
        if not all([webjob_user, webjob_pass]):
            return Response('Internal configuration error', 500)
        if not auth or not (auth.username == webjob_user and auth.password == webjob_pass):
            return Response('Unauthorized', 401, {'WWW-Authenticate': 'Basic realm="Login Required"'})
        return f(*args, **kwargs)
    return decorated

# --- WEBJOB ---
@admin_bp.route('/api/enforce_auto_clock_out', methods=['POST'])
@require_webjob_auth
def api_enforce_auto_clock_out():
    return jsonify(AdminService.run_auto_clock_out_logic()), 200

# --- DASHBOARD & VIEWS ---
@admin_bp.route('/dashboard')
@login_required
@admin_required
def dashboard():
    all_users = User.query.order_by(User.username).all()
    users_for_filter = [{'id': 0, 'username': _('All Users')}] + [{'id': u.id, 'username': u.username} for u in all_users]
    return render_template('admin/dashboard.html', users_for_filter=users_for_filter)

@admin_bp.route('/settings', methods=['GET', 'POST'])
@login_required
@admin_required
def settings():
    config = CompanyConfig.query.first_or_404()
    form = CompanyConfigForm(obj=config)
    if form.validate_on_submit():
        form.populate_obj(config)
        config.default_working_days = ','.join(form.default_working_days.data)
        db.session.commit()
        flash(_('Company settings updated successfully!'), 'success')
        return redirect(url_for('admin.settings'))
    if request.method == 'GET' and isinstance(config.default_working_days, str):
        form.default_working_days.data = config.default_working_days.split(',')
    return render_template('admin/settings.html', form=form)

# --- REPORTS ---
@admin_bp.route('/api/generate_report', methods=['GET'])
@login_required
@admin_required
def api_generate_report():
    try:
        args = AdminReportRequestArgs.model_validate(request.args.to_dict())
        user_ids = int(args.user_ids) if args.user_ids and args.user_ids != '0' else None
        data = AdminService.generate_detailed_report_json(None, user_ids, args.start_date, args.end_date)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@admin_bp.route('/reports', methods=['GET', 'POST'])
@login_required
@admin_required
def reports():
    form = AdminReportsForm(request.form)
    report_data, summary_data, report_type = None, None, None
    if request.method == 'POST' and form.validate():
        report_type = form.report_type.data
        report_data, summary_data = _calculate_report_metrics_for_period(report_type, form.user.data, form.start_date.data, form.end_date.data)
    return render_template('reports.html', form=form, report_data=report_data, summary_data=summary_data, report_type=report_type)

@admin_bp.route('/download_report')
@login_required
@admin_required
def download_report():
    try:
        start_date = datetime.strptime(request.args.get('start_date'), '%Y-%m-%d').date()
        end_date = datetime.strptime(request.args.get('end_date'), '%Y-%m-%d').date()
        csv_io = AdminService.generate_csv_output(
            request.args.get('report_type'), int(request.args.get('user_id', 0)), start_date, end_date
        )
        if not csv_io:
            flash(_("No data found to export."), "warning")
            return redirect(url_for('admin.reports'))
        return Response(csv_io, mimetype="text/csv", headers={"Content-Disposition": "attachment;filename=report.csv"})
    except Exception as e:
        flash(f"Error: {e}", "danger")
        return redirect(url_for('admin.reports'))

@admin_bp.route('/api/active_sessions')
@login_required
@admin_required
def api_active_sessions():
    return jsonify(AdminService.get_active_sessions_summary())

@admin_bp.route('/api/inactivity_report')
@login_required
@admin_required
def api_inactivity_report():
    try:
        u_ids = [int(x) for x in request.args.get('user_ids', '0').split(',') if x != '0']
        s_date = datetime.strptime(request.args.get('start_date'), '%Y-%m-%d').date()
        e_date = datetime.strptime(request.args.get('end_date'), '%Y-%m-%d').date()
        return jsonify(AdminService.get_inactivity_report(u_ids, s_date, e_date))
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@admin_bp.route('/api/historical_timeline')
@login_required
@admin_required
def api_historical_timeline():
    try:
        args = AdminHistoricalTimelineArgs.model_validate(request.args.to_dict())
        u_ids = [int(x) for x in args.user_ids.split(',') if x != '0']
        return jsonify(AdminService.get_historical_timeline(u_ids, args.date))
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@admin_bp.route('/api/all_active_users_timeline')
@login_required
@admin_required
def api_all_active_users_timeline():
    return jsonify(AdminService.get_all_active_users_timeline())

@admin_bp.route('/api/application_usage_report')
@login_required
@admin_required
def api_application_usage_report():
    try:
        u_ids = [int(x) for x in request.args.get('user_ids', '0').split(',') if x != '0']
        s_date = datetime.strptime(request.args.get('start_date'), '%Y-%m-%d').date()
        e_date = datetime.strptime(request.args.get('end_date'), '%Y-%m-%d').date()
        return jsonify(AdminService.get_app_usage_report(u_ids, s_date, e_date))
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@admin_bp.route('/api/daily_activity_summary')
@login_required
@admin_required
def api_daily_activity_summary():
    return jsonify(AdminService.get_daily_activity_summary())

# --- CORRECTIONS & ACTIONS ---
@admin_bp.route('/api/correct_time_entry', methods=['POST'])
@login_required
@admin_required
def api_correct_time_entry():
    try:
        d = request.get_json()
        AdminService.correct_time_entry(d.get('entry_id'), d.get('clock_in_time'), d.get('clock_out_time'))
        return jsonify({'success': True, 'message': 'Time entry corrected successfully'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@admin_bp.route('/api/manual_clock_out_all', methods=['POST'])
@login_required
@admin_required
def api_manual_clock_out_all():
    count = AdminService.manual_clock_out_all(current_user)
    return jsonify({'success': True, 'message': f'Successfully clocked out {count} users.'})

@admin_bp.route('/api/manual_clock_out/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def api_manual_clock_out(user_id):
    try:
        uname = AdminService.manual_clock_out_user(user_id, current_user)
        return jsonify({'success': True, 'message': f'User {uname} clocked out successfully'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@admin_bp.route('/api/update_idle_reason', methods=['POST'])
@login_required
@admin_required
def api_update_idle_reason():
    try:
        d = request.get_json()
        msg = AdminService.update_idle_reason(d.get('idle_entry_id'), d.get('new_type'))
        return jsonify({'success': True, 'message': msg})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@admin_bp.route('/api/convert_break_to_meeting', methods=['POST'])
@login_required
@admin_required
def api_convert_break_to_meeting():
    try:
        d = request.get_json()
        msg = AdminService.convert_break_to_meeting(d.get('break_entry_id'), d.get('convert_to'), current_user)
        return jsonify({'success': True, 'message': msg})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@admin_bp.route('/api/daily_user_entries')
@login_required
@admin_required
def api_daily_user_entries():
    return jsonify(AdminService.get_daily_entries(request.args.get('user_id', type=int), request.args.get('date')))

# --- OVERTIME ---
@admin_bp.route('/overtime')
@login_required
@admin_required
def overtime_management():
    return render_template('admin/overtime.html', users=User.query.order_by(User.username).all())

@admin_bp.route('/api/allocate_overtime', methods=['POST'])
@login_required
@admin_required
def api_allocate_overtime():
    try:
        AdminService.allocate_overtime(request.get_json(), current_user)
        return jsonify({'success': True, 'message': 'Overtime allocated successfully'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@admin_bp.route('/api/revoke_overtime/<int:allocation_id>', methods=['DELETE'])
@login_required
@admin_required
def api_revoke_overtime(allocation_id):
    try:
        AdminService.revoke_overtime(allocation_id)
        return jsonify({'success': True, 'message': 'Overtime revoked'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@admin_bp.route('/api/update_overtime_status/<int:allocation_id>', methods=['POST'])
@login_required
@admin_required
def api_update_overtime_status(allocation_id):
    try:
        status = request.get_json().get('status')
        if status not in ['approved', 'rejected']: return jsonify({'success': False}), 400
        AdminService.update_overtime_status(allocation_id, status, current_user)
        return jsonify({'success': True, 'message': f'Status updated to {status}'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@admin_bp.route('/api/user_overtime_allocations/<int:user_id>')
@login_required
@admin_required
def api_user_overtime_allocations(user_id):
    # Keep lightweight query here or move to service if strict
    allocs = OvertimeAllocation.query.filter_by(user_id=user_id).order_by(OvertimeAllocation.date.desc()).all()
    res = [{
        'id': a.id, 'date': a.date.strftime('%Y-%m-%d'), 'start_time': a.start_time.strftime('%H:%M'),
        'end_time': a.end_time.strftime('%H:%M'), 'reason': a.reason, 'status': a.status.capitalize(),
        'allocated_by': 'Admin', 'allocated_at': utc_to_berlin_filter(a.created_at, format='%Y-%m-%d %H:%M')
    } for a in allocs]
    return jsonify(res)

# --- HOLIDAYS ---
@admin_bp.route('/holidays', methods=['GET', 'POST'])
@login_required
@admin_required
def holidays():
    form = HolidayForm()
    if form.validate_on_submit():
        try:
            db.session.add(Holiday(name=form.name.data, date=form.date.data))
            db.session.commit()
            flash(_('Holiday added successfully!'), 'success')
            return redirect(url_for('admin.holidays'))
        except Exception:
            db.session.rollback()
            flash(_('Error adding holiday.'), 'danger')
    return render_template('admin/holidays.html', form=form, holidays=Holiday.query.order_by(Holiday.date.asc()).all())

@admin_bp.route('/holidays/delete/<int:holiday_id>', methods=['POST'])
@login_required
@admin_required
def delete_holiday(holiday_id):
    db.session.delete(Holiday.query.get_or_404(holiday_id))
    db.session.commit()
    flash(_('Holiday deleted.'), 'success')
    return redirect(url_for('admin.holidays'))

# --- PROVISIONS ---
@admin_bp.route('/provisions', methods=['GET', 'POST'])
@login_required
@admin_required
def manage_provisions():
    form = ProvisionForm()
    if form.validate_on_submit():
        p = Provision(user_id=form.user.data, beschreibung=form.beschreibung.data, zielvereinbarung=form.zielvereinbarung.data,
                      start_date=form.start_date.data, end_date=form.end_date.data, grenze_punkte=form.grenze_punkte.data,
                      typ=form.typ.data, provision_percent=form.provision_percent.data, provision_euro=form.provision_euro.data)
        db.session.add(p)
        db.session.commit()
        flash(_('New provision added!'), 'success')
        return redirect(url_for('admin.manage_provisions'))
        
    today = date.today()
    all_p = Provision.query.order_by(Provision.start_date.desc()).all()
    curr = [x for x in all_p if x.end_date >= today]
    exp = [x for x in all_p if x.end_date < today]
    return render_template('admin/provisions.html', form=form, current_provisions=curr, expired_provisions=exp,
                           users=User.query.filter_by(is_suspended=False).order_by(User.username).all(), title=_('Manage Provisions'))

@admin_bp.route('/api/provisions/<int:id>', methods=['GET'])
@login_required
@admin_required
def api_get_provision(id):
    p = Provision.query.get_or_404(id)
    return jsonify({'id': p.id, 'user_id': p.user_id, 'beschreibung': p.beschreibung, 'zielvereinbarung': p.zielvereinbarung,
                    'start_date': p.start_date.isoformat(), 'end_date': p.end_date.isoformat(), 'grenze_punkte': p.grenze_punkte,
                    'typ': p.typ, 'provision_percent': float(p.provision_percent) if p.provision_percent else None,
                    'provision_euro': float(p.provision_euro) if p.provision_euro else None})

@admin_bp.route('/api/provisions/<int:id>', methods=['PUT'])
@login_required
@admin_required
def api_update_provision_details(id):
    p = Provision.query.get_or_404(id)
    d = request.get_json()
    if 'beschreibung' in d: p.beschreibung = d['beschreibung']
    if 'zielvereinbarung' in d: p.zielvereinbarung = int(d['zielvereinbarung'])
    if 'start_date' in d: p.start_date = datetime.strptime(d['start_date'], '%Y-%m-%d').date()
    if 'end_date' in d: p.end_date = datetime.strptime(d['end_date'], '%Y-%m-%d').date()
    if 'grenze_punkte' in d: p.grenze_punkte = int(d['grenze_punkte'])
    if 'typ' in d: p.typ = d['typ']
    if 'provision_percent' in d and d['provision_percent']: p.provision_percent = float(d['provision_percent']); p.provision_euro = None
    elif 'provision_euro' in d and d['provision_euro']: p.provision_euro = float(d['provision_euro']); p.provision_percent = None
    db.session.commit()
    return jsonify({'success': True})

@admin_bp.route('/provisions/delete/<int:id>', methods=['POST'])
@login_required
@admin_required
def delete_provision(id):
    db.session.delete(Provision.query.get_or_404(id))
    db.session.commit()
    return redirect(url_for('admin.manage_provisions'))

@admin_bp.route('/api/update_provision', methods=['POST'])
@login_required
@admin_required
def api_update_provision():
    d = request.get_json()
    u = User.query.get(d.get('user_id'))
    if u:
        u.provision_percentage = float(d.get('provision'))
        db.session.commit()
        return jsonify({'success': True})
    return jsonify({'success': False}), 404

# --- LEAVES ---
@admin_bp.route('/admin/leaves', methods=['GET', 'POST'])
@login_required
@admin_required
def manage_leaves():
    if request.method == 'POST':
        req = LeaveRequest.query.get_or_404(request.form.get('request_id'))
        act = request.form.get('action')
        if act in ['approve', 'reject']:
            req.status = 'Approved' if act == 'approve' else 'Rejected'
            db.session.commit()
            flash(_('Leave request updated.'), 'success' if act == 'approve' else 'warning')
            return redirect(url_for('admin.manage_leaves'))
            
    reqs = LeaveRequest.query.order_by(LeaveRequest.status.desc(), LeaveRequest.start_date.desc()).all()
    return render_template('admin/leaves.html', leave_requests=reqs, upcoming_holidays=Holiday.query.filter(Holiday.date >= date.today()).all(), timedelta=timedelta)

# --- PASSWORDS & CRM ---
@admin_bp.route('/admin/passwords', methods=['GET', 'POST'])
@login_required
@admin_required
def manage_passwords():
    if request.method == 'POST':
        db.session.add(TelekomPassword(description=request.form.get('description'), password_value=request.form.get('password'),
                                       expiration_date=date.today() + timedelta(days=90)))
        db.session.commit()
        flash(_('Password added.'), 'success')
        return redirect(url_for('admin.manage_passwords'))
    return render_template('admin/passwords.html', passwords=TelekomPassword.query.order_by(TelekomPassword.created_at.desc()).all())

@admin_bp.route('/admin/passwords/delete/<int:id>', methods=['POST'])
@login_required
@admin_required
def delete_password(id):
    p = TelekomPassword.query.get_or_404(id)
    p.is_active = False
    db.session.commit()
    return redirect(url_for('admin.manage_passwords'))

@admin_bp.route('/admin/customers', methods=['GET', 'POST'])
@login_required
@admin_required
def manage_customers():
    if request.method == 'POST':
        cid = request.form.get('customer_id')
        name = request.form.get('company_name')
        stat = request.form.get('service_status')
        note = request.form.get('research_notes')
        
        if cid:
            c = Customer.query.get(cid)
            c.company_name = name; c.service_status = stat; c.research_notes = note
        else:
            db.session.add(Customer(company_name=name, service_status=stat, research_notes=note))
        db.session.commit()
        flash(_('Customer saved.'), 'success')
        return redirect(url_for('admin.manage_customers'))
    return render_template('admin/customers.html', customers=Customer.query.order_by(Customer.created_at.desc()).all())