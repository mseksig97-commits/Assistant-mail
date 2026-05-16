import base64
import json
import os
from datetime import datetime, timedelta
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from src.utils.logger import setup_logger

logger = setup_logger("google_calendar")

ALL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.labels",
    "https://www.googleapis.com/auth/calendar",
]


class GoogleCalendarClient:
    def __init__(self):
        self.token_file = os.getenv("GMAIL_TOKEN_FILE", "config/gmail_token.json")
        self.calendar_id = os.getenv("GOOGLE_CALENDAR_ID", "primary")
        self.service = None
        self._authenticate()

    def _authenticate(self):
        creds = self._load_creds()

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                self._save_creds(creds)
            else:
                raise RuntimeError("Google Calendar: no valid token. Run setup_auth.py first.")

        self.service = build("calendar", "v3", credentials=creds)
        logger.info("Google Calendar authenticated")

    def _load_creds(self) -> Optional[Credentials]:
        token_b64 = os.getenv("GMAIL_TOKEN_B64")
        if token_b64:
            try:
                info = json.loads(base64.b64decode(token_b64).decode())
                return Credentials.from_authorized_user_info(info, ALL_SCOPES)
            except Exception as e:
                logger.warning(f"Failed to load Calendar token from env: {e}")
        if os.path.exists(self.token_file):
            return Credentials.from_authorized_user_file(self.token_file, ALL_SCOPES)
        return None

    def _save_creds(self, creds: Credentials):
        try:
            os.makedirs(os.path.dirname(self.token_file), exist_ok=True)
            with open(self.token_file, "w") as f:
                f.write(creds.to_json())
        except Exception as e:
            logger.warning(f"Could not save Calendar token: {e}")

    def create_event(
        self,
        title: str,
        start_dt: datetime,
        end_dt: Optional[datetime] = None,
        description: str = "",
        all_day: bool = False,
    ) -> Optional[str]:
        if end_dt is None:
            end_dt = start_dt + timedelta(hours=1)

        tz = os.getenv("TIMEZONE", "Europe/Paris")

        if all_day:
            event_body = {
                "summary": title,
                "description": description,
                "start": {"date": start_dt.strftime("%Y-%m-%d")},
                "end": {"date": end_dt.strftime("%Y-%m-%d")},
            }
        else:
            event_body = {
                "summary": title,
                "description": description,
                "start": {"dateTime": start_dt.isoformat(), "timeZone": tz},
                "end": {"dateTime": end_dt.isoformat(), "timeZone": tz},
            }

        try:
            event = self.service.events().insert(
                calendarId=self.calendar_id, body=event_body
            ).execute()
            logger.info(f"Calendar event created: {title} ({event['id']})")
            return event["id"]
        except HttpError as e:
            logger.error(f"Calendar create error: {e}")
            return None

    def list_upcoming_events(self, max_results: int = 10) -> list[dict]:
        try:
            now = datetime.utcnow().isoformat() + "Z"
            result = self.service.events().list(
                calendarId=self.calendar_id,
                timeMin=now,
                maxResults=max_results,
                singleEvents=True,
                orderBy="startTime",
            ).execute()
            return result.get("items", [])
        except HttpError as e:
            logger.error(f"Calendar list error: {e}")
            return []
