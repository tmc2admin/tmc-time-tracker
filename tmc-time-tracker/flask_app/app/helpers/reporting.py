from datetime import datetime, timedelta, time
import pytz
from itertools import groupby
from collections import defaultdict
from flask_babel import gettext as _
from sqlalchemy.orm import joinedload
from ..models import db, User, TimeEntry
from .formatters import BERLIN_TZ, EPOCH_START_UTC, utc_to_berlin_filter, format_seconds_to_hhmm, format_datetime_for_report
from .calculations import calculate_worked_hours

def _get_consolidated_segments_for_user(user_id, start_date=None, end_date=None):
    MERGE_GAP_THRESHOLD_SECONDS = 5
    all_consolidated_periods = []
    
    translatable_reasons = {
        "System Idle": _("System Idle"),
        "User became active": _("User became active"),
        "In Meeting": _("In Meeting"),
        "Paused": _("Paused"),
        "Not Working": _("Not Working"),
        "Working": _("Working")
    }

    def translate_reason(reason_text):
        return translatable_reasons.get(reason_text, reason_text)

    query = TimeEntry.query.filter_by(user_id=user_id)
    if start_date:
        start_datetime_utc = pytz.utc.localize(datetime.combine(start_date, datetime.min.time()))
        query = query.filter(TimeEntry.clock_in_time >= start_datetime_utc)
    if end_date:
        end_datetime_utc = pytz.utc.localize(datetime.combine(end_date, datetime.max.time()))
        query = query.filter(TimeEntry.clock_in_time <= end_datetime_utc)

    time_entries = query.options(
        joinedload(TimeEntry.break_entries),
        joinedload(TimeEntry.idle_entries)
    ).order_by(TimeEntry.clock_in_time).all()

    for entry in time_entries:
        if not entry.clock_in_time: continue 
            
        clock_in_time_aware = pytz.utc.localize(entry.clock_in_time) if entry.clock_in_time.tzinfo is None else entry.clock_in_time
        if clock_in_time_aware <= EPOCH_START_UTC: continue

        if entry.clock_out_time:
            clock_out_time_aware = pytz.utc.localize(entry.clock_out_time) if entry.clock_out_time.tzinfo is None else entry.clock_out_time
        else:
            clock_out_time_aware = datetime.now(pytz.utc)
        
        events = [{'time': clock_in_time_aware, 'type': 'clock_in'}, {'time': clock_out_time_aware, 'type': 'clock_out'}]
        
        for b in entry.break_entries:
            if b.break_start_time:
                start_aware = pytz.utc.localize(b.break_start_time)
                if start_aware > EPOCH_START_UTC: events.append({'time': start_aware, 'type': 'break_start'})
            if b.break_end_time:
                end_aware = pytz.utc.localize(b.break_end_time)
                if end_aware > EPOCH_START_UTC: events.append({'time': end_aware, 'type': 'break_end'})
        
        for i in entry.idle_entries:
            if i.idle_start_time:
                start_aware = pytz.utc.localize(i.idle_start_time)
                if start_aware > EPOCH_START_UTC: events.append({'time': start_aware, 'type': 'idle_start', 'reason': i.reason})
            if i.idle_end_time:
                end_aware = pytz.utc.localize(i.idle_end_time)
                if end_aware > EPOCH_START_UTC: events.append({'time': end_aware, 'type': 'idle_end', 'reason': i.reason})
            
        events.sort(key=lambda x: x['time'])
        if not events: continue

        grouped_events = groupby(events, key=lambda x: x['time'])
        current_segment_start = events[0]['time']
        is_on_break, is_idle, current_idle_reason = False, False, None

        for timestamp, events_at_timestamp_iter in grouped_events:
            events_at_timestamp = list(events_at_timestamp_iter)

            if timestamp > current_segment_start:
                status = 'On Break' if is_on_break else ('In Meeting' if current_idle_reason == 'In Meeting' else 'Idle' if is_idle else 'Active')
                notes = ''
                if status == 'In Meeting': notes = translatable_reasons.get("In Meeting")
                elif status == 'On Break': notes = translatable_reasons.get("Paused")
                elif status == 'Idle': notes = translate_reason(current_idle_reason) or translatable_reasons.get("Not Working")
                elif status == 'Active': notes = translatable_reasons.get("Working")

                all_consolidated_periods.append({'start': current_segment_start, 'end': timestamp, 'status': status, 'notes': notes})

            for event in events_at_timestamp:
                if 'break' in event['type']: is_on_break = event['type'] == 'break_start'
                if 'idle' in event['type']:
                    is_idle = event['type'] == 'idle_start'
                    if is_idle:
                        current_idle_reason = event.get('reason')
                    else:
                        if all_consolidated_periods and all_consolidated_periods[-1]['status'] in ['Idle', 'In Meeting']:
                           reason = event.get('reason') or "User became active"
                           all_consolidated_periods[-1]['notes'] = translate_reason(reason)
                        current_idle_reason = None
            current_segment_start = timestamp

    final_report = []
    if all_consolidated_periods:
        all_consolidated_periods.sort(key=lambda x: x['start'])
        if not all_consolidated_periods: return []
        
        merged = [all_consolidated_periods[0]]
        for current in all_consolidated_periods[1:]:
            last = merged[-1]
            if current['status'] == last['status'] and (current['start'] - last['end']).total_seconds() <= MERGE_GAP_THRESHOLD_SECONDS:
                last['end'] = current['end']
            else:
                merged.append(current)
        final_report = merged

    return [{
        'start_time': seg['start'].isoformat(),
        'end_time': seg['end'].isoformat(),
        'status': seg['status'],
        'duration_minutes': round((seg['end'] - seg['start']).total_seconds() / 60, 1),
        'notes': seg['notes']
    } for seg in final_report if (seg['end'] - seg['start']).total_seconds() > 0]

def _calculate_report_metrics_for_period(report_type, user_id, start_date, end_date):
    start_datetime_utc = pytz.utc.localize(datetime.combine(start_date, time.min))
    end_datetime_utc = pytz.utc.localize(datetime.combine(end_date, time.max))
    
    user_list = []
    if user_id != 0:
        user = User.query.get(user_id)
        if user: user_list.append(user)
    else:
        user_list = User.query.order_by(User.username).all()

    query = TimeEntry.query.join(User).options(
        db.joinedload(TimeEntry.break_entries),
        db.joinedload(TimeEntry.idle_entries)
    ).filter(TimeEntry.clock_in_time.between(start_datetime_utc, end_datetime_utc)).order_by(User.username, TimeEntry.clock_in_time)

    if user_id != 0: query = query.filter(TimeEntry.user_id == user_id)

    entries = query.all()
    report_data = []
    now_utc = datetime.now(pytz.utc)

    if report_type == 'daily_summary':
        user_summary = defaultdict(lambda: {'total_productive_seconds': 0, 'total_break_seconds': 0, 'total_idle_seconds': 0})
        current_day = start_date
        while current_day <= end_date:
            day_start_berlin = datetime.combine(current_day, time.min)
            day_end_berlin = datetime.combine(current_day, time.max)
            day_start_utc = BERLIN_TZ.localize(day_start_berlin).astimezone(pytz.utc)
            day_end_utc = BERLIN_TZ.localize(day_end_berlin).astimezone(pytz.utc)

            for user in user_list:
                daily_entries = []
                for e in entries:
                    if e.user_id != user.id: continue
                    if e.clock_in_time:
                        clock_in_aware = e.clock_in_time.replace(tzinfo=pytz.utc)
                        if clock_in_aware >= day_start_utc and clock_in_aware <= day_end_utc:
                            daily_entries.append(e)

                if not daily_entries: continue

                total_gross_seconds = 0
                total_break_seconds = 0
                total_idle_seconds = 0

                first_clock_in_utc = min(e.clock_in_time for e in daily_entries)
                clock_out_times = [e.clock_out_time for e in daily_entries if e.clock_out_time]
                is_ongoing = any(e.clock_out_time is None for e in daily_entries)
                
                last_clock_out_utc = None
                last_clock_out_display = ""
                
                if is_ongoing:
                    if current_day == now_utc.astimezone(BERLIN_TZ).date():
                        last_clock_out_utc = now_utc.replace(tzinfo=None)
                        last_clock_out_display = _('Ongoing')
                    else:
                        last_clock_out_utc = day_end_utc.replace(tzinfo=None)
                        last_clock_out_display = _('Stale')
                elif clock_out_times:
                    last_clock_out_utc = max(clock_out_times)
                    last_clock_out_display = utc_to_berlin_filter(last_clock_out_utc, format='%H:%M')
                else:
                    last_clock_out_utc = first_clock_in_utc
                    last_clock_out_display = utc_to_berlin_filter(first_clock_in_utc, format='%H:%M')

                last_clock_out_aware = last_clock_out_utc.replace(tzinfo=pytz.utc)

                for entry in daily_entries:
                    entry_start_utc = entry.clock_in_time.replace(tzinfo=pytz.utc)
                    entry_end_utc = entry.clock_out_time.replace(tzinfo=pytz.utc) if entry.clock_out_time else last_clock_out_aware
                    total_gross_seconds += (entry_end_utc - entry_start_utc).total_seconds()

                    for b in entry.break_entries:
                        start_time = b.break_start_time.replace(tzinfo=pytz.utc)
                        end_time = b.break_end_time.replace(tzinfo=pytz.utc) if b.break_end_time else last_clock_out_aware
                        effective_start = max(start_time, entry_start_utc)
                        effective_end = min(end_time, entry_end_utc)
                        total_break_seconds += max(0, (effective_end - effective_start).total_seconds())

                    for i in entry.idle_entries:
                        if i.reason != "In Meeting":
                            start_time = i.idle_start_time.replace(tzinfo=pytz.utc)
                            end_time = i.idle_end_time.replace(tzinfo=pytz.utc) if i.idle_end_time else last_clock_out_aware
                            effective_start = max(start_time, entry_start_utc)
                            effective_end = min(end_time, entry_end_utc)
                            total_idle_seconds += max(0, (effective_end - effective_start).total_seconds())

                total_productive_seconds = total_gross_seconds - total_break_seconds - total_idle_seconds
                user_summary[user.username]['total_productive_seconds'] += total_productive_seconds
                user_summary[user.username]['total_break_seconds'] += total_break_seconds
                user_summary[user.username]['total_idle_seconds'] += total_idle_seconds

                report_data.append({
                    _('Date'): current_day.strftime('%d.%m.%Y'),
                    _('Username'): user.username,
                    _('Clock In'): utc_to_berlin_filter(first_clock_in_utc, format='%H:%M'),
                    _('Clock Out'): last_clock_out_display,
                    _('Net Hours'): format_seconds_to_hhmm(total_productive_seconds),
                    _('Break'): format_seconds_to_hhmm(total_break_seconds),
                    _('Idle'): format_seconds_to_hhmm(total_idle_seconds),
                })
            current_day += timedelta(days=1)
        
        report_data.sort(key=lambda x: (datetime.strptime(x[_('Date')], '%d.%m.%Y'), x[_('Username')]))
        summary_data = []
        for username, data in sorted(user_summary.items()):
            summary_data.append({
                _('Username'): username,
                _('Total Net Worked Hours'): format_seconds_to_hhmm(data['total_productive_seconds']),
                _('Total Break (Hours)'): format_seconds_to_hhmm(data['total_break_seconds']),
                _('Total Idle (Hours)'): format_seconds_to_hhmm(data['total_idle_seconds'])
            })
        return report_data, summary_data

    elif report_type == 'raw':
        now_naive = datetime.now(pytz.utc).replace(tzinfo=None)
        for entry in entries:
            gross_duration = (entry.clock_out_time - entry.clock_in_time).total_seconds() if entry.clock_out_time else 0
            break_seconds = sum(((b.break_end_time or now_naive) - b.break_start_time).total_seconds() for b in entry.break_entries)
            idle_seconds = sum(((i.idle_end_time or now_naive) - i.idle_start_time).total_seconds() for i in entry.idle_entries if i.reason != "In Meeting")
            
            report_data.append({
                _('Username'): entry.user.username,
                _('Clock In'): format_datetime_for_report(entry.clock_in_time),
                _('Clock Out'): format_datetime_for_report(entry.clock_out_time) if entry.clock_out_time else 'Ongoing',
                _('Gross Duration (Hours)'): format_seconds_to_hhmm(gross_duration),
                _('Total Break (Hours)'): format_seconds_to_hhmm(break_seconds),
                _('Total Idle (Hours)'): format_seconds_to_hhmm(idle_seconds),
                _('Net Worked Hours'): format_seconds_to_hhmm(calculate_worked_hours(entry) * 3600),
                _('Status'): entry.status
            })
        return report_data, None
    
    elif report_type == 'detail':
        segments = _get_consolidated_segments_for_user(user_id, start_date, end_date)
        for seg in segments:
             duration_min = ((datetime.fromisoformat(seg['end_time']) - datetime.fromisoformat(seg['start_time'])).total_seconds() / 60) if seg['end_time'] else 0
             report_data.append({
                _('Username'): User.query.get(user_id).username if user_id != 0 else 'Multiple Users',
                _('Start Time'): format_datetime_for_report(datetime.fromisoformat(seg['start_time'])),
                _('End Time'): format_datetime_for_report(datetime.fromisoformat(seg['end_time'])) if seg['end_time'] else 'Ongoing',
                _('Status'): seg['status'],
                _('Duration (Min)'): f"{duration_min:.2f}",
                _('Notes'): seg.get('notes', '')
             })
        return report_data, None

    return report_data, None