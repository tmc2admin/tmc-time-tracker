# app/services/web_service.py

import pytz
import logging
from datetime import datetime, time, timedelta
from sqlalchemy import or_
from sqlalchemy.orm import joinedload
from flask_babel import gettext as _

# Import Models & Helpers
from ..models import (db, TimeEntry, BreakEntry, IdleEntry, Holiday, 
                      OvertimeAllocation, LeaveRequest)
from ..helpers import (get_programmatic_status,
                       _get_consolidated_segments_for_user, utc_to_berlin_filter,
                       _handle_stale_sessions_for_user,
                       calculate_entries_time_totals)

BERLIN_TZ = pytz.timezone('Europe/Berlin')
log = logging.getLogger(__name__)


def _berlin_day_bounds(day):
    start = BERLIN_TZ.localize(datetime.combine(day, time.min)).astimezone(pytz.utc)
    next_day = day + timedelta(days=1)
    end = BERLIN_TZ.localize(datetime.combine(next_day, time.min)).astimezone(pytz.utc)
    return start, end


class WebService:

    # --- DASHBOARD & STATS ---

    @staticmethod
    def get_dashboard_stats(user):
        """Fetches lists and scalar stats for the dashboard render."""
        now_utc = datetime.now(pytz.utc)
        today = now_utc.astimezone(BERLIN_TZ).date()
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
            day_start_utc, day_end_utc = _berlin_day_bounds(today)
            today_entries = TimeEntry.query.options(
                joinedload(TimeEntry.break_entries),
                joinedload(TimeEntry.idle_entries)
            ).filter(
                TimeEntry.user_id == user.id,
                TimeEntry.clock_in_time < day_end_utc,
                or_(
                    TimeEntry.clock_out_time.is_(None),
                    TimeEntry.clock_out_time > day_start_utc
                )
            ).all()
            totals = calculate_entries_time_totals(
                today_entries,
                now_utc=now_utc,
                window_start=day_start_utc,
                window_end=day_end_utc
            )
            total_break = totals['pause_seconds']
            total_idle = totals['idle_seconds']
            gross_duration = totals['gross_seconds']
            remaining_seconds = max(
                0,
                getattr(user, 'default_daily_hours', 8.0) * 3600 - totals['net_seconds']
            )
            expected_out = (
                now_utc + timedelta(seconds=remaining_seconds)
            ).astimezone(BERLIN_TZ)

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

        today = now_utc.astimezone(BERLIN_TZ).date()
        day_start_utc, day_end_utc = _berlin_day_bounds(today)
        all_today = TimeEntry.query.options(
            joinedload(TimeEntry.break_entries),
            joinedload(TimeEntry.idle_entries)
        ).filter(
            TimeEntry.user_id == user.id,
            TimeEntry.clock_in_time < day_end_utc,
            or_(
                TimeEntry.clock_out_time.is_(None),
                TimeEntry.clock_out_time > day_start_utc
            )
        ).all()

        if not all_today: return data

        totals = calculate_entries_time_totals(
            all_today,
            now_utc=now_utc,
            window_start=day_start_utc,
            window_end=day_end_utc
        )
        first_in_utc = totals['first_clock_in']
        gross_seconds = totals['gross_seconds']
        non_prod_seconds = totals['pause_seconds']
        prod_seconds = totals['net_seconds']
        remaining_seconds = max(
            0,
            user.default_daily_hours * 3600 - prod_seconds
        )
        exp_out_time = now_utc + timedelta(seconds=remaining_seconds)
        active_clock_in = active_entry.clock_in_time
        if active_clock_in.tzinfo is None:
            active_clock_in = pytz.utc.localize(active_clock_in)
        else:
            active_clock_in = active_clock_in.astimezone(pytz.utc)

        data.update({
            'active_entry_id': active_entry.id,
            'status': get_programmatic_status(user.id, active_entry),
            'current_session_clock_in_time': active_clock_in.isoformat(),
            'first_clock_in_time_of_day': first_in_utc.isoformat(),
            'net_worked_hours': prod_seconds / 3600.0,
            'total_non_productive_seconds': non_prod_seconds,
            'total_gross_duration_seconds': gross_seconds,
            'expected_clock_out': exp_out_time.astimezone(BERLIN_TZ).strftime('%H:%M'),
            'total_break_seconds_today': totals['break_seconds'],
            'total_completed_idle_duration_seconds': totals['idle_seconds']
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
        start_dt, _ = _berlin_day_bounds(start_date)
        _, end_dt = _berlin_day_bounds(end_date)

        entries = TimeEntry.query.options(
            joinedload(TimeEntry.break_entries),
            joinedload(TimeEntry.idle_entries)
        ).filter(
            TimeEntry.user_id == user_id,
            TimeEntry.clock_in_time < end_dt,
            or_(
                TimeEntry.clock_out_time.is_(None),
                TimeEntry.clock_out_time > start_dt
            )
        ).all()

        if not entries:
            return {'gross_worked_hours': 0, 'productive_worked_hours': 0, 'total_break_time_hours': 0}

        totals = calculate_entries_time_totals(
            entries,
            window_start=start_dt,
            window_end=end_dt
        )

        return {
            'gross_worked_hours': totals['gross_seconds'] / 3600.0,
            'productive_worked_hours': totals['net_seconds'] / 3600.0,
            'total_break_time_hours': totals['pause_seconds'] / 3600.0
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
            day_start_utc, day_end_utc = _berlin_day_bounds(curr)
            
            entries = TimeEntry.query.options(joinedload(TimeEntry.break_entries), joinedload(TimeEntry.idle_entries))\
                .filter(
                    TimeEntry.user_id == user_id,
                    TimeEntry.clock_in_time < day_end_utc,
                    or_(
                        TimeEntry.clock_out_time.is_(None),
                        TimeEntry.clock_out_time > day_start_utc
                    )
                ).all()

            if not entries:
                curr += timedelta(days=1); continue

            totals = calculate_entries_time_totals(
                entries,
                now_utc=now_utc,
                window_start=day_start_utc,
                window_end=day_end_utc
            )
            first_in = totals['first_clock_in']
            last_out = totals['last_clock_out']

            is_ongoing = any(e.clock_out_time is None for e in entries)
            last_out_disp = _('Ongoing') if is_ongoing else utc_to_berlin_filter(last_out, format='%H:%M')

            report_data.append({
                'date': curr.strftime('%d.%m.%Y'),
                'clock_in': utc_to_berlin_filter(first_in, format='%H:%M'),
                'clock_out': last_out_disp,
                'gross_session_hours': totals['gross_seconds'] / 3600.0,
                'net_hours_worked': totals['net_seconds'] / 3600.0,
                'break_hours': totals['pause_seconds'] / 3600.0
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
