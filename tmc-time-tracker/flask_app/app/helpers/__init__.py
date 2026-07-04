# app/helpers/__init__.py

# Re-export everything from submodules to maintain backward compatibility
from .formatters import (
    BERLIN_TZ, 
    EPOCH_START_UTC, 
    utc_to_berlin_filter, 
    format_seconds_to_hhmm, 
    format_duration_filter, 
    format_datetime_for_report
)

from .calculations import (
    get_ongoing_duration, 
    calculate_total_duration, 
    calculate_break_duration, 
    calculate_idle_duration, 
    calculate_worked_hours, 
    calculate_gross_hours
)

from .status import (
    get_programmatic_status, 
    finalize_idle_period, 
    force_clock_out,
    is_user_globally_active
)

from .maintenance import (
    _handle_stale_sessions_for_user, 
    find_and_clock_out_zombie_sessions, 
    send_clock_out_email
)

from .reporting import (
    _get_consolidated_segments_for_user, 
    _calculate_report_metrics_for_period
)