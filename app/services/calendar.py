from __future__ import annotations

import base64
from datetime import datetime
from typing import List, Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build

from ..config import settings

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def _build_service():
    if not settings.google_credentials_json_base64:
        raise RuntimeError("google_credentials_missing")
    data = base64.b64decode(settings.google_credentials_json_base64)
    creds = service_account.Credentials.from_service_account_info(
        __import__("json").loads(data), scopes=SCOPES
    )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def check_availability(calendar_id: Optional[str], time_min: datetime, time_max: datetime) -> dict:
    cal_id = calendar_id or settings.google_calendar_id
    if not cal_id:
        raise RuntimeError("google_calendar_id_missing")
    service = _build_service()
    body = {
        "timeMin": time_min.isoformat(),
        "timeMax": time_max.isoformat(),
        "items": [{"id": cal_id}],
    }
    resp = service.freebusy().query(body=body).execute()
    return resp


def create_event(calendar_id: Optional[str], summary: str, start: datetime, end: datetime, attendees: Optional[List[str]] = None, description: Optional[str] = None) -> dict:
    cal_id = calendar_id or settings.google_calendar_id
    if not cal_id:
        raise RuntimeError("google_calendar_id_missing")
    service = _build_service()
    event = {
        "summary": summary,
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": end.isoformat()},
    }
    if attendees:
        event["attendees"] = [{"email": a} for a in attendees]
    if description:
        event["description"] = description
    return service.events().insert(calendarId=cal_id, body=event, sendUpdates="all").execute()


def list_events(calendar_id: Optional[str], time_min: Optional[datetime] = None, time_max: Optional[datetime] = None, max_results: int = 20) -> dict:
    cal_id = calendar_id or settings.google_calendar_id
    if not cal_id:
        raise RuntimeError("google_calendar_id_missing")
    service = _build_service()
    params = {"calendarId": cal_id, "maxResults": max_results, "singleEvents": True, "orderBy": "startTime"}
    if time_min:
        params["timeMin"] = time_min.isoformat()
    if time_max:
        params["timeMax"] = time_max.isoformat()
    return service.events().list(**params).execute()
