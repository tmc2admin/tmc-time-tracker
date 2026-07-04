# /flask_app/app/validation_models.py
from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List
from datetime import datetime, date, time

# --- Electron Client API Payloads ---

class SsoLoginPayload(BaseModel):
    microsoft_oid: str
    email: EmailStr
    username: str
    client_version: Optional[str] = None

class OidPayload(BaseModel):
    microsoft_oid: str

class LocationModel(BaseModel):
    ip: Optional[str] = None
    country: Optional[str] = None
    city: Optional[str] = None
    region: Optional[str] = None

class ClockInPayload(BaseModel):
    location: Optional[LocationModel] = None
    automatic: bool = False
    timestamp: datetime

class ClockOutPayload(BaseModel):
    source: str = Field(..., pattern=r'^(manual|auto|auto_break|auto_expiry|app_quit|server_auto_expiry|long_break)$')
    timestamp: datetime

class FinalizeIdlePayload(BaseModel):
    reason: str
    timestamp: datetime

class ActivityPayload(BaseModel):
    type: str
    user_id: int
    timestamp: datetime  
    metadata: Optional[dict] = None
    device_id: Optional[str] = None

class SystemSleepPayload(BaseModel):
    user_id: int
    duration_ms: int
    timestamp: datetime

class WebTokenPayload(BaseModel):
    user_id: int
    expires_in: Optional[str] = None

class ApplicationUsagePayload(BaseModel):
    user_id: int
    application_name: str
    window_title: str
    start_time: datetime
    end_time: datetime
    duration_seconds: int
    device_id: Optional[str] = None

class StateChangePayload(BaseModel):
    from_state: str
    to_state: str
    timestamp: datetime
    reason: Optional[str] = None
    user_id: int

# --- ADDED: The missing payload model ---
class BreakReasonPayload(BaseModel):
    reason: str

# --- Admin API Query Args ---

class AdminReportRequestArgs(BaseModel):
    user_ids: str  # Comma-separated
    start_date: date
    end_date: date
    granularity: str = Field(..., pattern=r'^(daily|weekly|monthly)$')

class AdminHistoricalTimelineArgs(BaseModel):
    user_ids: str
    date: date


