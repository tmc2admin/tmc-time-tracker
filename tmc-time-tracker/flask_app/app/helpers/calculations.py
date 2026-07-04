from datetime import datetime, timedelta
import pytz
# We import models only for type hinting or query construction if absolutely necessary
from ..models import BreakEntry, IdleEntry

def get_ongoing_duration(start_time):
    if start_time:
        start_time_aware = pytz.utc.localize(start_time) if start_time.tzinfo is None else start_time
        return datetime.now(pytz.utc) - start_time_aware
    return timedelta(0)

def calculate_total_duration(entries, start_attr, end_attr):
    total_duration = timedelta(0)
    for entry in entries:
        start_time = getattr(entry, start_attr)
        end_time = getattr(entry, end_attr)
        if start_time and end_time:
            start_aware = pytz.utc.localize(start_time) if start_time.tzinfo is None else start_time
            end_aware = pytz.utc.localize(end_time) if end_time.tzinfo is None else end_time
            total_duration += (end_aware - start_aware)
    return total_duration

def calculate_break_duration(time_entry_id):
    # Optimally, this should be refactored to accept an object to avoid this query, 
    # but kept as-is for strict compatibility.
    breaks = BreakEntry.query.filter(
        BreakEntry.time_entry_id == time_entry_id,
        BreakEntry.break_start_time.isnot(None),
        BreakEntry.break_end_time.isnot(None)
    ).all()
    return calculate_total_duration(breaks, 'break_start_time', 'break_end_time')

def calculate_idle_duration(time_entry_id):
    idles = IdleEntry.query.filter(
        IdleEntry.time_entry_id == time_entry_id,
        IdleEntry.idle_start_time.isnot(None),
        IdleEntry.idle_end_time.isnot(None),
        IdleEntry.reason != "In Meeting"  
    ).all()
    return calculate_total_duration(idles, 'idle_start_time', 'idle_end_time')

def calculate_worked_hours(time_entry):
    if not time_entry or not time_entry.clock_in_time:
        return 0.0

    now_utc = datetime.now(pytz.utc)
    clock_in_time = pytz.utc.localize(time_entry.clock_in_time) if time_entry.clock_in_time.tzinfo is None else time_entry.clock_in_time
    
    if time_entry.clock_out_time:
        end_time = pytz.utc.localize(time_entry.clock_out_time) if time_entry.clock_out_time.tzinfo is None else time_entry.clock_out_time
    else:
        end_time = now_utc

    gross_duration = (end_time - clock_in_time).total_seconds()
    
    total_break_seconds = 0
    if time_entry.break_entries: 
        for b in time_entry.break_entries:
            start = pytz.utc.localize(b.break_start_time) if b.break_start_time.tzinfo is None else b.break_start_time
            if b.break_end_time:
                end = pytz.utc.localize(b.break_end_time) if b.break_end_time.tzinfo is None else b.break_end_time
                total_break_seconds += (end - start).total_seconds()
            else:
                total_break_seconds += (now_utc - start).total_seconds()

    total_idle_seconds = 0
    if time_entry.idle_entries:
        for i in time_entry.idle_entries:
            if i.reason != "In Meeting":
                start = pytz.utc.localize(i.idle_start_time) if i.idle_start_time.tzinfo is None else i.idle_start_time
                if i.idle_end_time:
                    end = pytz.utc.localize(i.idle_end_time) if i.idle_end_time.tzinfo is None else i.idle_end_time
                    total_idle_seconds += (end - start).total_seconds()
                else:
                    total_idle_seconds += (now_utc - start).total_seconds()

    net_seconds = gross_duration - total_break_seconds - total_idle_seconds
    return max(0.0, round(net_seconds / 3600.0, 2))

def calculate_gross_hours(time_entry):
    if not time_entry or not time_entry.clock_in_time:
        return 0.0

    clock_in_time = pytz.utc.localize(time_entry.clock_in_time) if time_entry.clock_in_time.tzinfo is None else time_entry.clock_in_time
    
    if time_entry.clock_out_time:
        end_time = pytz.utc.localize(time_entry.clock_out_time) if time_entry.clock_out_time.tzinfo is None else time_entry.clock_out_time
    else:
        end_time = datetime.now(pytz.utc)

    gross_duration = end_time - clock_in_time
    return max(0.0, round(gross_duration.total_seconds() / 3600.0, 2))