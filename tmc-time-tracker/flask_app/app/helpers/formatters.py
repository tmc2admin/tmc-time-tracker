from datetime import datetime
import pytz

BERLIN_TZ = pytz.timezone('Europe/Berlin')
EPOCH_START_UTC = pytz.utc.localize(datetime(1970, 1, 1))

def utc_to_berlin_filter(dt_utc, format='%d.%m.%Y %H:%M:%S'):
    if not dt_utc:
        return 'N/A'
    if dt_utc.tzinfo is None:
        dt_utc = pytz.utc.localize(dt_utc)
    return dt_utc.astimezone(BERLIN_TZ).strftime(format)

def format_seconds_to_hhmm(seconds):
    if seconds is None:
        return '00:00'
    is_negative = seconds < 0
    seconds = abs(int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    sign = '-' if is_negative else ''
    return f"{sign}{int(hours):02d}:{int(minutes):02d}"

def format_duration_filter(seconds):
    if seconds is None:
        return 'N/A'
    hours, remainder = divmod(int(seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

def format_datetime_for_report(dt_utc):
    if not dt_utc:
        return None
    if dt_utc.tzinfo is None:
        dt_utc = pytz.utc.localize(dt_utc)
    return dt_utc.astimezone(BERLIN_TZ).strftime('%d.%m.%Y %H:%M')