from datetime import datetime, timedelta
import pytz
# We import models only for type hinting or query construction if absolutely necessary
from ..models import BreakEntry, IdleEntry


def _as_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return pytz.utc.localize(value)
    return value.astimezone(pytz.utc)


def _merge_intervals(intervals):
    merged = []
    for start, end in sorted(intervals, key=lambda interval: interval[0]):
        if end <= start:
            continue
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return merged


def _append_clamped_interval(intervals, start, end, boundary_start, boundary_end):
    start_utc = _as_utc(start)
    end_utc = _as_utc(end)
    if not start_utc or not end_utc:
        return

    effective_start = max(start_utc, boundary_start)
    effective_end = min(end_utc, boundary_end)
    if effective_end > effective_start:
        intervals.append((effective_start, effective_end))


def _total_interval_seconds(intervals):
    return sum((end - start).total_seconds() for start, end in intervals)


def calculate_entries_time_totals(
    entries,
    now_utc=None,
    window_start=None,
    window_end=None
):
    """
    Calculate consistent gross, pause, and net totals for one or more sessions.

    Gross is the union of clocked-in intervals. Pause is the union of explicit
    breaks and non-meeting idle intervals, clamped to those sessions.
    """
    now_utc = _as_utc(now_utc or datetime.now(pytz.utc))
    window_start = _as_utc(window_start) if window_start else None
    window_end = _as_utc(window_end) if window_end else None

    work_intervals = []
    break_intervals = []
    idle_intervals = []

    for entry in entries or []:
        entry_start = _as_utc(getattr(entry, 'clock_in_time', None))
        if not entry_start:
            continue

        entry_end = _as_utc(getattr(entry, 'clock_out_time', None)) or now_utc
        effective_start = max(entry_start, window_start) if window_start else entry_start
        effective_end = min(entry_end, window_end) if window_end else entry_end
        effective_end = min(effective_end, now_utc)
        if effective_end <= effective_start:
            continue

        work_intervals.append((effective_start, effective_end))

        for break_entry in getattr(entry, 'break_entries', []) or []:
            break_end = getattr(break_entry, 'break_end_time', None) or now_utc
            _append_clamped_interval(
                break_intervals,
                getattr(break_entry, 'break_start_time', None),
                break_end,
                effective_start,
                effective_end
            )

        for idle_entry in getattr(entry, 'idle_entries', []) or []:
            if getattr(idle_entry, 'reason', None) == "In Meeting":
                continue
            idle_end = getattr(idle_entry, 'idle_end_time', None) or now_utc
            _append_clamped_interval(
                idle_intervals,
                getattr(idle_entry, 'idle_start_time', None),
                idle_end,
                effective_start,
                effective_end
            )

    merged_work = _merge_intervals(work_intervals)
    merged_breaks = _merge_intervals(break_intervals)
    merged_idles = _merge_intervals(idle_intervals)
    merged_pauses = _merge_intervals(break_intervals + idle_intervals)

    gross_seconds = _total_interval_seconds(merged_work)
    pause_seconds = min(gross_seconds, _total_interval_seconds(merged_pauses))
    net_seconds = max(0.0, gross_seconds - pause_seconds)

    return {
        'gross_seconds': gross_seconds,
        'pause_seconds': pause_seconds,
        'net_seconds': net_seconds,
        'break_seconds': _total_interval_seconds(merged_breaks),
        'idle_seconds': _total_interval_seconds(merged_idles),
        'first_clock_in': merged_work[0][0] if merged_work else None,
        'last_clock_out': merged_work[-1][1] if merged_work else None
    }


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

    totals = calculate_entries_time_totals([time_entry])
    return round(totals['net_seconds'] / 3600.0, 2)

def calculate_gross_hours(time_entry):
    if not time_entry or not time_entry.clock_in_time:
        return 0.0

    totals = calculate_entries_time_totals([time_entry])
    return round(totals['gross_seconds'] / 3600.0, 2)
