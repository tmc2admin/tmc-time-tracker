# app/services/admin_service.py

import logging
import pytz
import json
import csv
from io import StringIO
from datetime import datetime, date, timedelta, time
from sqlalchemy import func, text, case, cast, Date, literal_column, and_
from sqlalchemy.orm import joinedload, aliased
from flask_babel import gettext as _

# Import models & db
from ..models import (db, User, TimeEntry, BreakEntry, IdleEntry, CompanyConfig, 
                      ApplicationUsage, Holiday, Provision, LeaveRequest, 
                      Customer, TelekomPassword, ActivityLog, OvertimeAllocation, UserLocationLog)

# --- REFACTORED IMPORTS FROM NEW HELPERS PACKAGE ---
from ..helpers.status import get_programmatic_status, finalize_idle_period, force_clock_out
from ..helpers.calculations import calculate_worked_hours
from ..helpers.formatters import utc_to_berlin_filter
from ..helpers.reporting import _get_consolidated_segments_for_user, _calculate_report_metrics_for_period

BERLIN_TZ = pytz.timezone('Europe/Berlin')
log = logging.getLogger(__name__)

class AdminService:
    
    # --- WEBJOBS & AUTOMATION ---
    @staticmethod
    def run_auto_clock_out_logic():
        """Logic for the WebJob to enforce auto clock-outs and heartbeat checks."""
        summary = {'long_break_clock_outs': 0, 'heartbeat_breaks_started': 0}
        
        # 1. Long Break Checks
        try:
            config = CompanyConfig.query.first()
            if config and config.auto_clock_out_after_break_minutes and config.auto_clock_out_after_break_minutes > 0:
                now_utc = datetime.now(pytz.utc)
                threshold_delta = timedelta(minutes=config.auto_clock_out_after_break_minutes)
                ongoing_breaks = BreakEntry.query.filter(BreakEntry.break_end_time.is_(None)).all()

                for break_entry in ongoing_breaks:
                    start_time_utc = pytz.utc.localize(break_entry.break_start_time) if break_entry.break_start_time.tzinfo is None else break_entry.break_start_time
                    if (now_utc - start_time_utc) > threshold_delta:
                        clock_out_time = start_time_utc + threshold_delta
                        force_clock_out(entry=break_entry.time_entry, reason="auto_clocked_out_long_break", clock_out_time=clock_out_time)
                        summary['long_break_clock_outs'] += 1
        except Exception as e:
            log.exception(f"Error in long break check: {e}")

        # 2. Heartbeat Checks
        try:
            timeout = timedelta(minutes=15)
            now_utc = datetime.now(pytz.utc)
            active_entries = TimeEntry.query.filter(TimeEntry.clock_out_time.is_(None)).all()

            for entry in active_entries:
                if get_programmatic_status(entry.user.id, is_admin_view=True) != 'Active':
                    continue

                last_activity_naive = db.session.query(func.max(ApplicationUsage.end_time)).filter(
                    ApplicationUsage.user_id == entry.user_id,
                    ApplicationUsage.start_time >= entry.clock_in_time
                ).scalar()

                last_seen = pytz.utc.localize(last_activity_naive) if last_activity_naive else (pytz.utc.localize(entry.clock_in_time) if entry.clock_in_time.tzinfo is None else entry.clock_in_time)

                if (now_utc - last_seen) > timeout:
                    break_start = (last_seen + timeout).replace(tzinfo=None)
                    db.session.add(BreakEntry(time_entry_id=entry.id, break_start_time=break_start, reason=_("System Idle (Heartbeat Timeout)")))
                    summary['heartbeat_breaks_started'] += 1
        except Exception as e:
            log.exception(f"Error in heartbeat check: {e}")

        if summary['long_break_clock_outs'] > 0 or summary['heartbeat_breaks_started'] > 0:
            db.session.commit()
            
        return summary

    # --- DASHBOARD & MONITORING ---
    @staticmethod
    def get_active_sessions_summary():
        """Optimized query for all active sessions."""
        now_utc = datetime.now(pytz.utc)
        today_utc_date = now_utc.date()

        # Fetch latest active entry per user
        active_entries = TimeEntry.query.filter(TimeEntry.clock_out_time.is_(None)).options(
            joinedload(TimeEntry.user), joinedload(TimeEntry.break_entries), joinedload(TimeEntry.idle_entries)
        ).all()
        
        # Deduplicate
        latest_map = {}
        for entry in active_entries:
            if entry.user_id not in latest_map or entry.clock_in_time > latest_map[entry.user_id].clock_in_time:
                latest_map[entry.user_id] = entry
        
        active_list = list(latest_map.values())
        active_user_ids = {e.user_id for e in active_list}

        # Calculate metrics for today (Performance Fix: In-Memory)
        daily_entries = TimeEntry.query.filter(
            TimeEntry.user_id.in_(active_user_ids),
            cast(TimeEntry.clock_in_time, Date) == today_utc_date
        ).options(joinedload(TimeEntry.break_entries), joinedload(TimeEntry.idle_entries)).all()

        user_metrics = {}
        for entry in daily_entries:
            uid = entry.user_id
            if uid not in user_metrics: user_metrics[uid] = {'work': 0, 'non_work': 0}
            
            end_time = entry.clock_out_time or now_utc.replace(tzinfo=None)
            gross = (end_time - entry.clock_in_time).total_seconds()
            
            breaks = sum(((b.break_end_time or now_utc.replace(tzinfo=None)) - b.break_start_time).total_seconds() for b in entry.break_entries)
            idle = sum(((i.idle_end_time or now_utc.replace(tzinfo=None)) - i.idle_start_time).total_seconds() for i in entry.idle_entries if i.reason != 'In Meeting')
            
            user_metrics[uid]['non_work'] += (breaks + idle)
            user_metrics[uid]['work'] += max(0, gross - breaks - idle)

        # Get Locations
        locations = {}
        for uid in active_user_ids:
            loc = UserLocationLog.query.filter(UserLocationLog.time_entry_id == latest_map[uid].id).first()
            locations[uid] = f"{loc.city}, {loc.country}" if loc and loc.city else "NA"

        # Build Response
        sessions_data = []
        status_counts = {"Active": 0, "On Break": 0, "Idle": 0, "Inactive": 0} # Default keys

        for entry in active_list:
            status = get_programmatic_status(entry.user.id, is_admin_view=True)
            status_counts[status] = status_counts.get(status, 0) + 1
            
            metrics = user_metrics.get(entry.user_id, {'work': 0, 'non_work': 0})
            
            # Simple approximation of first clock in for display
            first_in = min([e.clock_in_time for e in daily_entries if e.user_id == entry.user_id], default=entry.clock_in_time)
            first_in_utc = pytz.utc.localize(first_in) if first_in.tzinfo is None else first_in
            
            expected_out = first_in_utc + timedelta(hours=entry.user.default_daily_hours) + timedelta(seconds=metrics['non_work'])
            
            sessions_data.append({
                'username': entry.user.username,
                'first_clock_in_time': first_in_utc.astimezone(BERLIN_TZ).strftime('%H:%M'),
                'status': status,
                'location': locations.get(entry.user_id, "NA"),
                'app_version': entry.client_version or 'N/A',
                'expected_clock_out': expected_out.astimezone(BERLIN_TZ).strftime('%H:%M')
            })
            
        total_users = db.session.query(func.count(User.id)).scalar()
        
        return {
            'active_sessions': sorted(sessions_data, key=lambda x: x['username']),
            'status_counts': status_counts,
            'total_users': total_users,
            'inactive_users_count': total_users - len(active_list)
        }

    @staticmethod
    def get_inactivity_report(user_ids, start_date, end_date):
        start_dt = pytz.utc.localize(datetime.combine(start_date, time.min))
        end_dt = pytz.utc.localize(datetime.combine(end_date, time.max))
        
        time_entry_alias = aliased(TimeEntry)

        # Query Breaks
        break_q = db.session.query(
            User.username.label('username'), literal_column("'On Break'").label('type'),
            BreakEntry.id.label('specific_id'), time_entry_alias.id.label('entry_id'),
            BreakEntry.break_start_time.label('start'), BreakEntry.break_end_time.label('end'),
            func.coalesce(BreakEntry.reason, '').label('notes')
        ).select_from(BreakEntry).join(time_entry_alias, BreakEntry.time_entry_id == time_entry_alias.id)\
         .join(User, time_entry_alias.user_id == User.id).filter(BreakEntry.break_start_time.between(start_dt, end_dt))

        # Query Idle
        idle_q = db.session.query(
            User.username.label('username'), 
            case((IdleEntry.reason.like('In Meeting%'), 'In Meeting'), else_='Idle').label('type'),
            IdleEntry.id.label('specific_id'), time_entry_alias.id.label('entry_id'),
            IdleEntry.idle_start_time.label('start'), IdleEntry.idle_end_time.label('end'),
            IdleEntry.reason.label('notes')
        ).select_from(IdleEntry).join(time_entry_alias, IdleEntry.time_entry_id == time_entry_alias.id)\
         .join(User, time_entry_alias.user_id == User.id).filter(IdleEntry.idle_start_time.between(start_dt, end_dt))

        if user_ids:
            break_q = break_q.filter(User.id.in_(user_ids))
            idle_q = idle_q.filter(User.id.in_(user_ids))

        results = break_q.union_all(idle_q).order_by(text('start DESC')).all()
        
        data = []
        translatable = {
            "In Meeting": _("In Meeting"), "System Idle": _("System Idle"), 
            "Break started": _("Break started"), "Clock-out": _("Clock-out"),
            "Admin manual clock-out": _("Admin manual clock-out")
        }

        for r in results:
            data.append({
                'username': r.username, 'type': r.type, 'translated_type': _(r.type),
                'start_time': pytz.utc.localize(r.start).isoformat() if r.start else None,
                'duration_seconds': (pytz.utc.localize(r.end) - pytz.utc.localize(r.start)).total_seconds() if r.end and r.start else 0,
                'notes': translatable.get(r.notes, r.notes),
                'time_entry_id': r.entry_id, 'specific_entry_id': r.specific_id
            })
        return data

    @staticmethod
    def get_historical_timeline(user_ids, date_obj):
        users = User.query.all() if not user_ids else User.query.filter(User.id.in_(user_ids)).all()
        result = [{'username': u.username, 'segments': _get_consolidated_segments_for_user(u.id, date_obj, date_obj)} for u in users]
        return {'users': [u for u in result if u['segments']]}

    @staticmethod
    def get_all_active_users_timeline():
        today = datetime.now(pytz.utc).date()
        active_users = db.session.query(TimeEntry.user_id, User.username).join(User).filter(TimeEntry.clock_out_time.is_(None)).distinct().all()
        data = []
        for uid, uname in active_users:
            segs = _get_consolidated_segments_for_user(uid, today, today)
            if segs: data.append({'username': uname, 'segments': segs})
        return {'users': data}

    @staticmethod
    def get_app_usage_report(user_ids, start_date, end_date):
        start_dt = pytz.utc.localize(datetime.combine(start_date, time.min))
        end_dt = pytz.utc.localize(datetime.combine(end_date, time.max))
        q = db.session.query(
            ApplicationUsage.application_name,
            func.sum(ApplicationUsage.duration_seconds).label('duration'),
            func.count(ApplicationUsage.id).label('count')
        ).filter(ApplicationUsage.start_time.between(start_dt, end_dt))
        
        if user_ids: q = q.filter(ApplicationUsage.user_id.in_(user_ids))
        
        results = q.group_by(ApplicationUsage.application_name).order_by(text('duration DESC')).all()
        return [{'application_name': r.application_name, 'total_duration_seconds': r.duration, 'interaction_count': r.count} for r in results]

    @staticmethod
    def get_daily_activity_summary():
        now_utc = datetime.now(pytz.utc)
        today_start = datetime.combine(now_utc.date(), time.min)
        
        users = User.query.options(joinedload(User.time_entries.and_(TimeEntry.clock_in_time >= today_start))
                                  .options(joinedload(TimeEntry.break_entries), joinedload(TimeEntry.idle_entries))
                                  ).filter(User.time_entries.any(TimeEntry.clock_in_time >= today_start)).all()
        summary = []
        for u in users:
            entries = u.time_entries
            if not entries: continue
            first_in = min(e.clock_in_time for e in entries)
            is_ongoing = any(e.clock_out_time is None for e in entries)
            last_out = None if is_ongoing else max(e.clock_out_time for e in entries if e.clock_out_time)
            end_calc = last_out if last_out else now_utc.replace(tzinfo=None)
            
            gross = (end_calc - first_in).total_seconds()
            inactive = 0
            for e in entries:
                b_sum = sum(((b.break_end_time or now_utc.replace(tzinfo=None)) - b.break_start_time).total_seconds() for b in e.break_entries)
                i_sum = sum(((i.idle_end_time or now_utc.replace(tzinfo=None)) - i.idle_start_time).total_seconds() for i in e.idle_entries if i.reason != 'In Meeting')
                inactive += (b_sum + i_sum)
                
            summary.append({
                'username': u.username,
                'first_clock_in': pytz.utc.localize(first_in).isoformat(),
                'last_clock_out': pytz.utc.localize(last_out).isoformat() if last_out else 'Ongoing',
                'gross_duration_seconds': max(0, gross),
                'inactive_duration_seconds': max(0, inactive),
                'net_duration_seconds': max(0, gross - inactive)
            })
        return sorted(summary, key=lambda x: x['username'])

    # --- REPORTS & EXPORTS ---
    @staticmethod
    def generate_detailed_report_json(report_type, user_id, start_date, end_date):
        # Uses the helper function logic, but centralized here
        try:
            berlin_tz = pytz.timezone('Europe/Berlin')
            now_utc = datetime.now(pytz.utc)
            period_start = berlin_tz.localize(datetime.combine(start_date, time.min)).astimezone(pytz.utc)
            period_end = berlin_tz.localize(datetime.combine(end_date, time.max)).astimezone(pytz.utc)
            
            users = User.query.filter(User.id == user_id).all() if user_id else User.query.all()
            user_ids = [u.id for u in users]
            
            entries = TimeEntry.query.options(joinedload(TimeEntry.break_entries), joinedload(TimeEntry.idle_entries))\
                .filter(TimeEntry.user_id.in_(user_ids), TimeEntry.clock_in_time >= period_start, TimeEntry.clock_in_time <= period_end)\
                .order_by(TimeEntry.clock_in_time).all()
                
            # Grouping Logic
            grouped = {} # {uid: {date: [entries]}}
            for e in entries:
                d_key = pytz.utc.localize(e.clock_in_time).astimezone(berlin_tz).strftime('%Y-%m-%d')
                if e.user_id not in grouped: grouped[e.user_id] = {}
                if d_key not in grouped[e.user_id]: grouped[e.user_id][d_key] = []
                grouped[e.user_id][d_key].append(e)
                
            data = []
            curr = start_date
            while curr <= end_date:
                d_key = curr.strftime('%Y-%m-%d')
                disp_date = curr.strftime('%d.%m.%Y')
                for u in users:
                    daily = grouped.get(u.id, {}).get(d_key, [])
                    if daily:
                        first_in_utc = min(e.clock_in_time for e in daily)
                        clock_outs = [e.clock_out_time for e in daily if e.clock_out_time]
                        is_ongoing = any(e.clock_out_time is None for e in daily)
                        
                        last_out_calc = None
                        last_out_disp = "N/A"
                        
                        if is_ongoing:
                            if curr == now_utc.astimezone(berlin_tz).date():
                                last_out_calc = now_utc.replace(tzinfo=None)
                                last_out_disp = _('Ongoing')
                            else:
                                last_out_calc = berlin_tz.localize(datetime.combine(curr, time.max)).astimezone(pytz.utc).replace(tzinfo=None)
                                last_out_disp = _('Stale')
                        elif clock_outs:
                            last_out_calc = max(clock_outs)
                            last_out_disp = utc_to_berlin_filter(last_out_calc, format='%H:%M')
                        else:
                            last_out_calc = first_in_utc
                            last_out_disp = utc_to_berlin_filter(first_in_utc, format='%H:%M')
                            
                        gross = (last_out_calc - first_in_utc).total_seconds()
                        breaks = 0
                        idle = 0
                        for e in daily:
                            e_end = e.clock_out_time or last_out_calc
                            breaks += sum(((b.break_end_time or e_end) - b.break_start_time).total_seconds() for b in e.break_entries)
                            idle += sum(((i.idle_end_time or e_end) - i.idle_start_time).total_seconds() for i in e.idle_entries if i.reason != 'In Meeting')
                            
                        net_hrs = max(0, gross - breaks - idle) / 3600.0
                        
                        data.append({
                            'Datum': disp_date, 'Benutzername': u.username,
                            'ErsterLogin': utc_to_berlin_filter(first_in_utc, format='%H:%M'),
                            'LetzterLogout': last_out_disp,
                            'NettoStunden': round(net_hrs, 2),
                            'GesamtPause': breaks, 'GesamtLeerlauf': idle
                        })
                curr += timedelta(days=1)
            return data
        except Exception as e:
            log.error(f"Generate Report Error: {e}")
            raise e

    @staticmethod
    def generate_csv_output(report_type, user_id, start_date, end_date):
        data, _ = _calculate_report_metrics_for_period(report_type, user_id, start_date, end_date)
        if not data: return None
        
        output = StringIO()
        writer = csv.writer(output, delimiter=';')
        writer.writerow(data[0].keys())
        for row in data:
            writer.writerow(row.values())
        output.seek(0)
        return output

    # --- ACTIONS & CORRECTIONS ---
    @staticmethod
    def correct_time_entry(entry_id, clock_in_str, clock_out_str):
        entry = TimeEntry.query.get_or_404(entry_id)
        if clock_in_str:
            local = BERLIN_TZ.localize(datetime.strptime(clock_in_str, '%Y-%m-%dT%H:%M'))
            entry.clock_in_time = local.astimezone(pytz.utc).replace(tzinfo=None)
        if clock_out_str:
            if clock_out_str.lower() == 'ongoing':
                entry.clock_out_time = None
            else:
                local = BERLIN_TZ.localize(datetime.strptime(clock_out_str, '%Y-%m-%dT%H:%M'))
                entry.clock_out_time = local.astimezone(pytz.utc).replace(tzinfo=None)
                
        if entry.clock_out_time:
            entry.total_worked_hours = calculate_worked_hours(entry)
        else:
            entry.total_worked_hours = 0.0
        db.session.commit()

    @staticmethod
    def manual_clock_out_all(admin_user):
        active_entries = TimeEntry.query.filter_by(clock_out_time=None).all()
        now_utc = datetime.now(pytz.utc)
        count = 0
        for entry in active_entries:
            BreakEntry.query.filter_by(time_entry_id=entry.id, break_end_time=None).update({'break_end_time': now_utc.replace(tzinfo=None)})
            for i in IdleEntry.query.filter_by(time_entry_id=entry.id, idle_end_time=None).all():
                finalize_idle_period(i, now_utc, reason="Admin manual clock-out (All)", commit=False)
            
            entry.clock_out_time = now_utc.replace(tzinfo=None)
            entry.status = 'admin_clocked_out'
            entry.total_worked_hours = calculate_worked_hours(entry)
            
            db.session.add(ActivityLog(user_id=admin_user.id, time_entry_id=entry.id, timestamp=now_utc.replace(tzinfo=None),
                                       event_type='admin_clock_out_all', details=json.dumps({'admin': admin_user.username, 'target': entry.user.username})))
            count += 1
        db.session.commit()
        return count

    @staticmethod
    def manual_clock_out_user(user_id, admin_user):
        user = User.query.get_or_404(user_id)
        entry = TimeEntry.query.filter_by(user_id=user_id, clock_out_time=None).first()
        if not entry: raise ValueError("User is not clocked in")
        
        now_utc = datetime.now(pytz.utc)
        BreakEntry.query.filter_by(time_entry_id=entry.id, break_end_time=None).update({'break_end_time': now_utc.replace(tzinfo=None)})
        for i in IdleEntry.query.filter_by(time_entry_id=entry.id, idle_end_time=None).all():
            finalize_idle_period(i, now_utc, reason="Admin manual clock-out", commit=True)
            
        entry.clock_out_time = now_utc.replace(tzinfo=None)
        entry.status = 'admin_clocked_out'
        entry.total_worked_hours = calculate_worked_hours(entry)
        
        db.session.add(ActivityLog(user_id=admin_user.id, time_entry_id=entry.id, timestamp=now_utc.replace(tzinfo=None),
                                   event_type='admin_clock_out', details=json.dumps({'admin': admin_user.username, 'target': user.username})))
        db.session.commit()
        return user.username

    @staticmethod
    def update_idle_reason(idle_id, new_type):
        entry = IdleEntry.query.get_or_404(idle_id)
        if new_type == 'Active':
            db.session.delete(entry)
            msg = 'Idle period converted to Active (entry removed).'
        else:
            entry.reason = 'In Meeting' if new_type == 'In Meeting' else 'System Idle'
            msg = 'Entry updated successfully.'
        db.session.commit()
        return msg

    @staticmethod
    def convert_break_to_meeting(break_id, convert_to, admin_user):
        break_entry = BreakEntry.query.get_or_404(break_id)
        time_entry = break_entry.time_entry
        admin_note = f" (Converted from break by {admin_user.username})"
        
        if convert_to == 'In Meeting':
            new_reason = f"In Meeting{admin_note}"
            db.session.add(IdleEntry(time_entry_id=time_entry.id, idle_start_time=break_entry.break_start_time,
                                     idle_end_time=break_entry.break_end_time, reason=new_reason))
            db.session.delete(break_entry)
            msg = 'Break converted to meeting.'
        elif convert_to == 'Active':
            if break_entry.break_start_time < time_entry.clock_in_time:
                time_entry.clock_in_time = break_entry.break_start_time
            if time_entry.clock_out_time and break_entry.break_end_time and break_entry.break_end_time > time_entry.clock_out_time:
                time_entry.clock_out_time = break_entry.break_end_time
                
            # Reopen session if needed
            if time_entry.clock_out_time and break_entry.break_end_time and abs((time_entry.clock_out_time - break_entry.break_end_time).total_seconds()) < 60:
                time_entry.clock_out_time = None
                time_entry.status = 'active'
                
            db.session.delete(break_entry)
            msg = 'Break converted to active time.'
        else:
            raise ValueError('Invalid conversion type')
            
        db.session.commit()
        time_entry.total_worked_hours = calculate_worked_hours(time_entry)
        db.session.commit()
        return msg

    @staticmethod
    def get_daily_entries(user_id, date_str):
        try:
            target = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError: return []
        
        start = BERLIN_TZ.localize(datetime.combine(target, time.min)).astimezone(pytz.utc)
        end = BERLIN_TZ.localize(datetime.combine(target + timedelta(days=1), time.min)).astimezone(pytz.utc)
        
        entries = TimeEntry.query.filter(TimeEntry.user_id == user_id, TimeEntry.clock_in_time >= start, TimeEntry.clock_in_time < end).options(joinedload(TimeEntry.user)).all()
        result = []
        for e in entries:
            in_utc = pytz.utc.localize(e.clock_in_time) if e.clock_in_time.tzinfo is None else e.clock_in_time
            out_utc = pytz.utc.localize(e.clock_out_time) if e.clock_out_time and e.clock_out_time.tzinfo is None else None
            result.append({
                'id': e.id, 'username': e.user.username,
                'clock_in_time': in_utc.isoformat() if in_utc else None,
                'clock_out_time': out_utc.isoformat() if out_utc else None
            })
        return result

    # --- OVERTIME, HOLIDAYS, PROVISIONS, LEAVES, PASSWORDS ---
    @staticmethod
    def allocate_overtime(data, admin_user):
        user = User.query.get_or_404(data['user_id'])
        start = datetime.strptime(data['start_time'], '%H:%M').time()
        end = datetime.strptime(data['end_time'], '%H:%M').time()
        alloc_date = datetime.strptime(data['date'], '%Y-%m-%d').date()
        
        allocation = OvertimeAllocation.query.filter_by(user_id=user.id, date=alloc_date).first()
        if allocation:
            allocation.start_time = start; allocation.end_time = end; allocation.reason = data.get('reason', '')
            allocation.allocated_by = admin_user.id; allocation.status = 'approved'
        else:
            db.session.add(OvertimeAllocation(user_id=user.id, allocated_by=admin_user.id, date=alloc_date,
                                              start_time=start, end_time=end, reason=data.get('reason', ''), status='approved'))
        
        # Live Update
        today = datetime.now(BERLIN_TZ).date()
        if alloc_date == today:
            local_dt = BERLIN_TZ.localize(datetime.combine(alloc_date, end))
            user.overtime_end_time = local_dt.astimezone(pytz.utc).replace(tzinfo=None)
            
        db.session.commit()

    @staticmethod
    def revoke_overtime(alloc_id):
        alloc = OvertimeAllocation.query.get_or_404(alloc_id)
        # Clear live update if applicable
        today = datetime.now(BERLIN_TZ).date()
        local_dt = BERLIN_TZ.localize(datetime.combine(alloc.date, alloc.end_time))
        utc_end = local_dt.astimezone(pytz.utc).replace(tzinfo=None)
        if alloc.user.overtime_end_time == utc_end: alloc.user.overtime_end_time = None
        
        db.session.delete(alloc)
        db.session.commit()

    @staticmethod
    def update_overtime_status(alloc_id, status, admin_user):
        alloc = OvertimeAllocation.query.get_or_404(alloc_id)
        alloc.status = status
        alloc.allocated_by = admin_user.id
        
        today = datetime.now(BERLIN_TZ).date()
        local_dt = BERLIN_TZ.localize(datetime.combine(alloc.date, alloc.end_time))
        utc_end = local_dt.astimezone(pytz.utc).replace(tzinfo=None)
        
        if alloc.date == today:
            if status == 'approved': alloc.user.overtime_end_time = utc_end
            elif status == 'rejected' and alloc.user.overtime_end_time == utc_end: alloc.user.overtime_end_time = None
        db.session.commit()