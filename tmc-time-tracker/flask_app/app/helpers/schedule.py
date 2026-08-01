from datetime import datetime, time


DEFAULT_WORKING_DAYS = (
    'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'
)


def normalize_time(value, fallback):
    if value is None:
        return fallback
    if isinstance(value, time):
        return value
    if isinstance(value, datetime):
        return value.time()
    if isinstance(value, str):
        raw_value = value.split('.')[0]
        for time_format in ('%H:%M:%S', '%H:%M'):
            try:
                return datetime.strptime(raw_value, time_format).time()
            except ValueError:
                continue
    return fallback


def get_user_schedule(user, company_config=None):
    company_start = normalize_time(
        getattr(company_config, 'working_hours_start', None),
        time(8, 0)
    )
    company_end = normalize_time(
        getattr(company_config, 'working_hours_end', None),
        time(17, 0)
    )

    start_time = normalize_time(
        getattr(user, 'session_start_time', None),
        company_start
    )
    end_time = normalize_time(
        getattr(user, 'session_end_time', None),
        company_end
    )

    raw_days = getattr(user, 'default_working_days', None)
    if not raw_days and company_config:
        raw_days = getattr(company_config, 'default_working_days', None)

    working_days = tuple(
        day.strip() for day in (raw_days or '').split(',') if day.strip()
    ) or DEFAULT_WORKING_DAYS

    return start_time, end_time, working_days


def is_within_user_schedule(user, company_config, local_datetime):
    start_time, end_time, working_days = get_user_schedule(user, company_config)
    return (
        local_datetime.strftime('%A') in working_days
        and start_time <= local_datetime.time().replace(tzinfo=None) <= end_time
    )
