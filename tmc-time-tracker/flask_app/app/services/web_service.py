# app/services/web_service.py

import json
import pytz
import logging
from datetime import datetime, time, timedelta
from sqlalchemy import func, cast, Date, and_
from sqlalchemy.orm import joinedload
from flask_babel import gettext as _

# Import Models & Helpers
from ..models import (db, TimeEntry, BreakEntry, IdleEntry, Holiday, 
                      OvertimeAllocation, LeaveRequest, User)
from ..helpers import (calculate_break_duration, calculate_idle_duration, 
                       get_ongoing_duration, get_programmatic_status, 
                       _get_consolidated_segments_for_user, utc_to_berlin_filter,
                       _handle_stale_sessions_for_user)

BERLIN_TZ = pytz.timezone('Europe/Berlin')
log = logging.getLogger(__name__)

class WebService:

    # --- DASHBOARD & STATS ---

    @staticmethod
    @staticmethod
    @staticmethod
    def get_dashboard_stats(user):
        """Fetches lists and scalar stats for the dashboard render."""
        today = datetime.utcnow().date()
        current_year = today.year
        
        # ... (Recent Overtime & Recent Leaves queries remain the same) ...
        recent_ot = OvertimeAllocation.query.filter(
            OvertimeAllocation.user_id == user.id,
            OvertimeAllocation.date >= (today - timedelta(days=30))
        ).order_by(OvertimeAllocation.date.desc()).limit(5).all()

        recent_leaves = LeaveRequest.query.filter(
            LeaveRequest.user_id == user.id
        ).order_by(LeaveRequest.created_at.desc()).limit(5).all()

        # --- FIX: Calculate Leave Balance (Business Days Only) ---
        approved_leaves = LeaveRequest.query.filter(
            LeaveRequest.user_id == user.id,
            LeaveRequest.status == 'Approved',
            LeaveRequest.start_date >= datetime(current_year, 1, 1).date()
        ).all()
        
        def count_business_days(start_date, end_date):
            """Counts days excluding Saturday (5) and Sunday (6)."""
            day_count = 0
            curr = start_date
            while curr <= end_date:
                # 0=Mon, 1=Tue, ..., 4=Fri, 5=Sat, 6=Sun
                if curr.weekday() < 5: 
                    day_count += 1
                curr += timedelta(days=1)
            return day_count

        # Use the helper function instead of simple subtraction
        days_taken = sum(count_business_days(r.start_date, r.end_date) for r in approved_leaves)
        
        days_remaining = max(0, getattr(user, 'annual_leave_days', 30) - days_taken)
        # ---------------------------------------------------------

        # ... (Rest of the function: Holidays, Active Status etc. remains unchanged) ...
        holidays = Holiday.query.filter(Holiday.date >= today).order_by(Holiday.date.asc()).limit(5).all()

        active_entry = TimeEntry.query.filter_by(user_id=user.id, clock_out_time=None).first()
        active_break = None
        total_break = 0
        total_idle = 0
        gross_duration = 0
        expected_out = None
        display_status = 'Inactive'

        if active_entry:
            display_status = 'Active'
            active_break = BreakEntry.query.filter_by(time_entry_id=active_entry.id, break_end_time=None).first()
            total_break = calculate_break_duration(active_entry.id).total_seconds()
            total_idle = calculate_idle_duration(active_entry.id).total_seconds()
            
            ongoing_idle = IdleEntry.query.filter_by(time_entry_id=active_entry.id, idle_end_time=None).first()
            if ongoing_idle:
                total_idle += get_ongoing_duration(ongoing_idle.idle_start_time).total_seconds()
            
            now_utc = pytz.utc.localize(datetime.utcnow())
            start_dt = pytz.utc.localize(active_entry.clock_in_time) if active_entry.clock_in_time.tzinfo is None else active_entry.clock_in_time
            gross_duration = (now_utc - start_dt).total_seconds()
            expected_out = start_dt + timedelta(hours=getattr(user, 'default_daily_hours', 8.0))

        return {
            'recent_ot': recent_ot,
            'recent_leaves': recent_leaves,
            'leave_days_taken': days_taken,
            'leave_days_remaining': days_remaining,
            'upcoming_holidays': holidays,
            'active_entry': active_entry,
            'active_break': active_break,
            'total_break_seconds': total_break,
            'total_idle_seconds': total_idle,
            'gross_duration_seconds': gross_duration,
            'expected_clock_out': expected_out,
            'user_display_status': display_status
        }

    @staticmethod
    def get_live_dashboard_data(user):
        """Calculates granular real-time data for the JS frontend updater."""
        _handle_stale_sessions_for_user(user.id)
        now_utc = datetime.now(pytz.utc)
        
        active_entry = TimeEntry.query.filter_by(user_id=user.id, clock_out_time=None).first()

        # Default Offline Data
        data = {
            'status': 'Offline', 
            'net_worked_hours': 0.0,
            'expected_clock_out': 'N/A',
            # ... initialize other fields with 0/None as per original code ...
            'active_entry_id': None, 'current_session_clock_in_time': None, 'first_clock_in_time_of_day': None,
            'total_non_productive_seconds': 0, 'total_gross_duration_seconds': 0, 
            'total_break_seconds_today': 0, 'total_completed_idle_duration_seconds': 0
        }

        if not active_entry:
            return data

        # Fetch all work for today to aggregate totals
        workday_start = active_entry.clock_in_time.date()
        all_today = TimeEntry.query.filter(
            TimeEntry.user_id == user.id,
            cast(TimeEntry.clock_in_time, Date) >= workday_start
        ).all()

        if not all_today: return data

        # Aggregate Metrics
        first_in = min(e.clock_in_time for e in all_today)
        total_break = 0
        total_idle = 0 # Non-meeting
        total_meeting = 0

        for entry in all_today:
            # Sum Breaks
            for b in entry.break_entries:
                end = b.break_end_time or now_utc.replace(tzinfo=None)
                total_break += (end - b.break_start_time).total_seconds()
            # Sum Idle
            for i in entry.idle_entries:
                end = i.idle_end_time or now_utc.replace(tzinfo=None)
                dur = (end - i.idle_start_time).total_seconds()
                if i.reason != "In Meeting": total_idle += dur
                else: total_meeting += dur

        first_in_utc = pytz.utc.localize(first_in) if first_in.tzinfo is None else first_in
        
        # P1d: Gross (First In -> Now)
        gross_seconds = (now_utc - first_in_utc).total_seconds()
        
        # P1c: Non-Productive
        non_prod_seconds = total_break + total_idle
        
        # P1b: Productive
        prod_seconds = gross_seconds - non_prod_seconds

        # Expected Out
        exp_out_time = first_in_utc + timedelta(hours=user.default_daily_hours) + timedelta(seconds=(total_break + total_idle))
        
        data.update({
            'active_entry_id': active_entry.id,
            'status': get_programmatic_status(user.id, active_entry),
            'current_session_clock_in_time': pytz.utc.localize(active_entry.clock_in_time).isoformat(),
            'first_clock_in_time_of_day': first_in_utc.isoformat(),
            'net_worked_hours': prod_seconds / 3600.0,
            'total_non_productive_seconds': non_prod_seconds,
            'total_gross_duration_seconds': gross_seconds,
            'expected_clock_out': exp_out_time.astimezone(BERLIN_TZ).strftime('%H:%M'),
            'total_break_seconds_today': total_break,
            'total_completed_idle_duration_seconds': total_idle
        })

        # Add ongoing timestamps for UI tickers
        active_break = BreakEntry.query.filter_by(time_entry_id=active_entry.id, break_end_time=None).first()
        if active_break: 
            data['current_ongoing_break_start_time'] = pytz.utc.localize(active_break.break_start_time).isoformat()
            
        active_idle = IdleEntry.query.filter_by(time_entry_id=active_entry.id, idle_end_time=None).first()
        if active_idle:
            data['current_ongoing_idle_start_time'] = pytz.utc.localize(active_idle.idle_start_time).isoformat()

        return data

    # --- REPORTS & TIMELINE ---

    @staticmethod
    def get_daily_summary(user_id, start_date, end_date):
        """Calculates gross, productive, and break hours for a date range."""
        start_dt = pytz.utc.localize(datetime.combine(start_date, time.min))
        end_dt = pytz.utc.localize(datetime.combine(end_date, time.max))

        entries = TimeEntry.query.filter(
            TimeEntry.user_id == user_id,
            TimeEntry.clock_in_time >= start_dt, TimeEntry.clock_in_time <= end_dt
        ).options(joinedload(TimeEntry.break_entries), joinedload(TimeEntry.idle_entries)).all()

        if not entries:
            return {'gross_worked_hours': 0, 'productive_worked_hours': 0, 'total_break_time_hours': 0}

        total_gross = 0
        total_break = 0
        total_idle = 0
        now_utc = datetime.now(pytz.utc)

        for e in entries:
            e_start = e.clock_in_time.replace(tzinfo=pytz.utc)
            e_end = e.clock_out_time.replace(tzinfo=pytz.utc) if e.clock_out_time else now_utc
            
            total_gross += (e_end - e_start).total_seconds()

            # Sum Breaks
            for b in e.break_entries:
                b_start = b.break_start_time.replace(tzinfo=pytz.utc)
                b_end = b.break_end_time.replace(tzinfo=pytz.utc) if b.break_end_time else now_utc
                # Clamp
                eff_start = max(b_start, e_start)
                eff_end = min(b_end, e_end)
                total_break += max(0, (eff_end - eff_start).total_seconds())

            # Sum Idle (excluding meetings)
            for i in e.idle_entries:
                if i.reason != "In Meeting":
                    i_start = i.idle_start_time.replace(tzinfo=pytz.utc)
                    i_end = i.idle_end_time.replace(tzinfo=pytz.utc) if i.idle_end_time else now_utc
                    eff_start = max(i_start, e_start)
                    eff_end = min(i_end, e_end)
                    total_idle += max(0, (eff_end - eff_start).total_seconds())

        return {
            'gross_worked_hours': total_gross / 3600.0,
            'productive_worked_hours': max(0, total_gross - total_break - total_idle) / 3600.0,
            'total_break_time_hours': total_break / 3600.0
        }

    @staticmethod
    def get_historical_entries_consolidated(user_id, start_date, end_date):
        """Fetches, filters, and merges timeline segments for Gantt chart."""
        raw_data = _get_consolidated_segments_for_user(user_id, start_date, end_date)
        if not raw_data: return []

        # 1. Filter out pure Idle (keep meetings/work/breaks)
        filtered = [s for s in raw_data if s.get('status') != 'Idle']
        if not filtered: return []

        # 2. Merge Logic
        merged = []
        MERGE_TOLERANCE = 2
        
        for seg in filtered:
            if not merged:
                merged.append(seg)
                continue
            
            last = merged[-1]
            try:
                last_end = datetime.fromisoformat(last['end_time'])
                curr_start = datetime.fromisoformat(seg['start_time'])
                curr_end = datetime.fromisoformat(seg['end_time'])
                
                # Check match and gap
                if (seg.get('status') == last.get('status') and 
                    abs((curr_start - last_end).total_seconds()) <= MERGE_TOLERANCE):
                    
                    # Merge
                    last['end_time'] = seg['end_time']
                    last_start = datetime.fromisoformat(last['start_time'])
                    last['duration_minutes'] = (curr_end - last_start).total_seconds() / 60.0
                    if seg.get('notes') and not last.get('notes'): last['notes'] = seg.get('notes')
                else:
                    merged.append(seg)
            except (ValueError, TypeError):
                merged.append(seg)

        # 3. Translate
        for m in merged:
            m['status'] = _(m['status'])
            
        return merged

    @staticmethod
    def get_daily_time_report_list(user_id, start_date, end_date):
        """Generates the day-by-day rows for the frontend report table."""
        report_data = []
        curr = start_date
        now_utc = datetime.now(pytz.utc)

        while curr <= end_date:
            day_start_utc = BERLIN_TZ.localize(datetime.combine(curr, time.min)).astimezone(pytz.utc)
            day_end_utc = BERLIN_TZ.localize(datetime.combine(curr, time.max)).astimezone(pytz.utc)
            
            entries = TimeEntry.query.options(joinedload(TimeEntry.break_entries), joinedload(TimeEntry.idle_entries))\
                .filter(TimeEntry.user_id == user_id, TimeEntry.clock_in_time >= day_start_utc, TimeEntry.clock_in_time <= day_end_utc).all()

            if not entries:
                curr += timedelta(days=1); continue

            # Calc Day Bounds
            first_in = min(e.clock_in_time.replace(tzinfo=pytz.utc) for e in entries)
            
            end_candidates = [e.clock_out_time.replace(tzinfo=pytz.utc) if e.clock_out_time else now_utc for e in entries]
            last_out = min(max(end_candidates), day_end_utc) # Cap at EOD

            # Display
            is_ongoing = any(e.clock_out_time is None for e in entries)
            last_out_disp = _('Ongoing') if is_ongoing else utc_to_berlin_filter(last_out, format='%H:%M')

            total_gross = max(0, (last_out - first_in).total_seconds())
            total_break = 0
            total_idle = 0

            # Calc Non-Productive (Summed per entry)
            for e in entries:
                e_start = e.clock_in_time.replace(tzinfo=pytz.utc)
                e_end = e.clock_out_time.replace(tzinfo=pytz.utc) if e.clock_out_time else last_out
                e_end = min(e_end, day_end_utc)

                # Helper to calc overlap
                def calc_overlap(s, e, boundary_s, boundary_e):
                    eff_s = max(s, boundary_s)
                    eff_e = min(e, boundary_e)
                    return max(0, (eff_e - eff_s).total_seconds())

                for b in e.break_entries:
                    b_s = b.break_start_time.replace(tzinfo=pytz.utc)
                    b_e = b.break_end_time.replace(tzinfo=pytz.utc) if b.break_end_time else last_out
                    total_break += calc_overlap(b_s, b_e, e_start, e_end)

                for i in e.idle_entries:
                    if i.reason != "In Meeting":
                        i_s = i.idle_start_time.replace(tzinfo=pytz.utc)
                        i_e = i.idle_end_time.replace(tzinfo=pytz.utc) if i.idle_end_time else last_out
                        total_idle += calc_overlap(i_s, i_e, e_start, e_end)

            productive = total_gross - total_break - total_idle
            
            report_data.append({
                'date': curr.strftime('%d.%m.%Y'),
                'clock_in': utc_to_berlin_filter(first_in, format='%H:%M'),
                'clock_out': last_out_disp,
                'gross_session_hours': total_gross / 3600.0,
                'net_hours_worked': max(0, productive) / 3600.0,
                'break_hours': total_break / 3600.0
            })
            curr += timedelta(days=1)
            
        return report_data

    # --- ACTIONS & REQUESTS ---

    @staticmethod
    def submit_overtime_request(user, data):
        """Validates and creates overtime request."""
        date_str = data.get('date')
        start_str = data.get('start_time')
        end_str = data.get('end_time')
        
        if not all([date_str, start_str, end_str]):
            raise ValueError(_('Date, Start Time and End Time are required.'))

        req_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        start = datetime.strptime(start_str, '%H:%M').time()
        end = datetime.strptime(end_str, '%H:%M').time()
        
        if start >= end:
            raise ValueError(_('End time must be after start time.'))

        # Overlap Check
        overlap = OvertimeAllocation.query.filter(
            OvertimeAllocation.user_id == user.id, OvertimeAllocation.date == req_date,
            OvertimeAllocation.start_time < end, OvertimeAllocation.end_time > start,
            OvertimeAllocation.status.in_(['Pending', 'Approved'])
        ).first()

        if overlap:
            raise ValueError(_('This request overlaps with an existing overtime entry.'))

        db.session.add(OvertimeAllocation(user_id=user.id, date=req_date, start_time=start, end_time=end,
                                          reason=data.get('reason', ''), status='Pending'))
        db.session.commit()
        return _('Overtime request submitted successfully!')

    @staticmethod
    def submit_leave_request(user, form_data):
        """Validates and creates leave request."""
        s_str = form_data.get('start_date')
        e_str = form_data.get('end_date')
        
        if not s_str or not e_str: raise ValueError(_('Please select both start and end dates.'))
        
        start = datetime.strptime(s_str, '%Y-%m-%d').date()
        end = datetime.strptime(e_str, '%Y-%m-%d').date()
        
        if end < start: raise ValueError(_('End date cannot be before start date.'))
        
        # --- NEW VALIDATION: Block Weekends ---
        # .weekday() returns 0=Monday ... 5=Saturday, 6=Sunday
        if start.weekday() >= 5:
            raise ValueError(_('Start date cannot be a weekend (Saturday/Sunday).'))
        
        if end.weekday() >= 5:
            raise ValueError(_('End date cannot be a weekend (Saturday/Sunday).'))
        # --------------------------------------

        db.session.add(LeaveRequest(user_id=user.id, start_date=start, end_date=end,
                                    reason=form_data.get('reason'), status='Pending'))
        db.session.commit()
        return _('Leave request submitted successfully.')