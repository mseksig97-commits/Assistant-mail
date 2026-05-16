import os
from datetime import datetime, timedelta
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from src.utils.logger import setup_logger

logger = setup_logger("google_calendar")

SCOPES = ["https://www.googleapis.com/auth/calendar"]


class GoogleCalendarClient:
    def __init__(self):
        self.credentials_file = os.getenv("GMAIL_CREDENTIALS_FILE", "config/gmail_credentials.json")
        self.token_file = os.getenv("GMAIL_TOKEN_FILE", "config/gmail_token.json")
        self.calendar_id = os.getenv("GOOGLE_CALENDAR_ID", "primary")
        self.service = None
        self._authenticate()

    def _authenticate(self):
        creds = None
        all_scopes = [
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/gmail.labels",
            "https://www.googleapis.com/auth/calendar",
        ]
        if os.path.exists(self.token_file):
            creds = Credentials.from_authorized_user_file(self.token_file, all_scopes)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(self.credentials_file, all_scopes)
                creds = flow.run_local_server(port=0)
            with open(self.token_file, "w") as f:
                f.write(creds.to_json())

        self.service = build("calendar", "v3", credentials=creds)
        logger.info("Google Calendar authenticated")

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
