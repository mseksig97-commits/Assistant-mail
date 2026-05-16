import os
import base64
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional
from datetime import datetime

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from src.utils.logger import setup_logger

logger = setup_logger("gmail_client")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.labels",
]


class GmailClient:
    def __init__(self):
        self.credentials_file = os.getenv("GMAIL_CREDENTIALS_FILE", "config/gmail_credentials.json")
        self.token_file = os.getenv("GMAIL_TOKEN_FILE", "config/gmail_token.json")
        self.service = None
        self._authenticate()

    def _authenticate(self):
        creds = None
        if os.path.exists(self.token_file):
            creds = Credentials.from_authorized_user_file(self.token_file, SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(self.credentials_file, SCOPES)
                creds = flow.run_local_server(port=0)
            with open(self.token_file, "w") as f:
                f.write(creds.to_json())

        self.service = build("gmail", "v1", credentials=creds)
        logger.info("Gmail authenticated successfully")

    def list_emails(self, max_results: int = 50, query: str = "") -> list[dict]:
        try:
            result = self.service.users().messages().list(
                userId="me", maxResults=max_results, q=query
            ).execute()
            messages = result.get("messages", [])
            emails = []
            for msg in messages:
                email = self.get_email(msg["id"])
                if email:
                    emails.append(email)
            return emails
        except HttpError as e:
            logger.error(f"Gmail list error: {e}")
            return []

    def get_email(self, msg_id: str) -> Optional[dict]:
        try:
            msg = self.service.users().messages().get(
                userId="me", id=msg_id, format="full"
            ).execute()
            headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
            body = self._extract_body(msg["payload"])
            return {
                "id": msg_id,
                "account": "gmail",
                "subject": headers.get("Subject", "(no subject)"),
                "from": headers.get("From", ""),
                "to": headers.get("To", ""),
                "date": headers.get("Date", ""),
                "body": body,
                "labels": msg.get("labelIds", []),
                "snippet": msg.get("snippet", ""),
            }
        except HttpError as e:
            logger.error(f"Gmail get_email error for {msg_id}: {e}")
            return None

    def _extract_body(self, payload: dict) -> str:
        if "body" in payload and payload["body"].get("data"):
            return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
        if "parts" in payload:
            for part in payload["parts"]:
                if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                    return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
            for part in payload["parts"]:
                text = self._extract_body(part)
                if text:
                    return text
        return ""

    def send_email(self, to: str, subject: str, body: str, reply_to_id: Optional[str] = None) -> bool:
        try:
            msg = MIMEMultipart()
            msg["to"] = to
            msg["subject"] = subject
            msg.attach(MIMEText(body, "plain", "utf-8"))

            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            body_payload: dict = {"raw": raw}
            if reply_to_id:
                body_payload["threadId"] = reply_to_id

            self.service.users().messages().send(userId="me", body=body_payload).execute()
            logger.info(f"Gmail: sent email to {to}")
            return True
        except HttpError as e:
            logger.error(f"Gmail send error: {e}")
            return False

    def apply_label(self, msg_id: str, label_name: str) -> bool:
        try:
            label_id = self._get_or_create_label(label_name)
            self.service.users().messages().modify(
                userId="me", id=msg_id,
                body={"addLabelIds": [label_id]}
            ).execute()
            return True
        except HttpError as e:
            logger.error(f"Gmail label error: {e}")
            return False

    def _get_or_create_label(self, name: str) -> str:
        result = self.service.users().labels().list(userId="me").execute()
        for label in result.get("labels", []):
            if label["name"].lower() == name.lower():
                return label["id"]
        new_label = self.service.users().labels().create(
            userId="me", body={"name": name}
        ).execute()
        return new_label["id"]

    def mark_as_read(self, msg_id: str) -> bool:
        try:
            self.service.users().messages().modify(
                userId="me", id=msg_id,
                body={"removeLabelIds": ["UNREAD"]}
            ).execute()
            return True
        except HttpError as e:
            logger.error(f"Gmail mark_as_read error: {e}")
            return False
