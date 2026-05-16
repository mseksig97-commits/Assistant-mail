import os
import json
import requests
from typing import Optional
from datetime import datetime, timedelta

import msal

from src.utils.logger import setup_logger

logger = setup_logger("outlook_client")

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
SCOPES = ["https://graph.microsoft.com/Mail.ReadWrite", "https://graph.microsoft.com/Mail.Send",
          "https://graph.microsoft.com/Calendars.ReadWrite"]


class OutlookClient:
    def __init__(self, account_num: int):
        """account_num: 1 or 2"""
        prefix = f"OUTLOOK{account_num}_"
        self.client_id = os.getenv(f"{prefix}CLIENT_ID")
        self.client_secret = os.getenv(f"{prefix}CLIENT_SECRET")
        self.tenant_id = os.getenv(f"{prefix}TENANT_ID", "common")
        self.email = os.getenv(f"{prefix}EMAIL")
        self.token_file = os.getenv(f"{prefix}TOKEN_FILE", f"config/outlook{account_num}_token.json")
        self.account_label = f"outlook{account_num}"
        self._token_cache = msal.SerializableTokenCache()
        self._app = None
        self._load_token_cache()
        self._build_app()

    def _load_token_cache(self):
        if os.path.exists(self.token_file):
            with open(self.token_file) as f:
                self._token_cache.deserialize(f.read())

    def _save_token_cache(self):
        if self._token_cache.has_state_changed:
            os.makedirs(os.path.dirname(self.token_file), exist_ok=True)
            with open(self.token_file, "w") as f:
                f.write(self._token_cache.serialize())

    def _build_app(self):
        self._app = msal.ConfidentialClientApplication(
            self.client_id,
            authority=f"https://login.microsoftonline.com/{self.tenant_id}",
            client_credential=self.client_secret,
            token_cache=self._token_cache,
        )

    def get_auth_url(self) -> str:
        """Returns the URL the user must visit to authorize the app (first-time setup)."""
        flow = self._app.initiate_auth_code_flow(SCOPES, redirect_uri="http://localhost:8080")
        # Store flow state so complete_auth can use it
        with open(self.token_file + ".flow", "w") as f:
            json.dump(flow, f)
        return flow["auth_uri"]

    def complete_auth(self, redirect_response_url: str) -> bool:
        flow_file = self.token_file + ".flow"
        if not os.path.exists(flow_file):
            logger.error("No pending auth flow found")
            return False
        with open(flow_file) as f:
            flow = json.load(f)
        # Parse the response
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(redirect_response_url)
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        result = self._app.acquire_token_by_auth_code_flow(flow, params)
        if "access_token" in result:
            self._save_token_cache()
            os.remove(flow_file)
            logger.info(f"Outlook {self.account_label} authenticated")
            return True
        logger.error(f"Outlook auth error: {result.get('error_description')}")
        return False

    def _get_token(self) -> Optional[str]:
        accounts = self._app.get_accounts()
        result = None
        if accounts:
            result = self._app.acquire_token_silent(SCOPES, account=accounts[0])
        if not result or "access_token" not in result:
            logger.warning(f"Outlook {self.account_label}: token expired or missing, re-auth needed")
            return None
        self._save_token_cache()
        return result["access_token"]

    def _headers(self) -> Optional[dict]:
        token = self._get_token()
        if not token:
            return None
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def list_emails(self, max_results: int = 50, folder: str = "inbox") -> list[dict]:
        headers = self._headers()
        if not headers:
            return []
        url = f"{GRAPH_BASE}/me/mailFolders/{folder}/messages?$top={max_results}&$orderby=receivedDateTime desc"
        try:
            r = requests.get(url, headers=headers, timeout=30)
            r.raise_for_status()
            emails = []
            for msg in r.json().get("value", []):
                emails.append(self._parse_message(msg))
            return emails
        except Exception as e:
            logger.error(f"Outlook list error: {e}")
            return []

    def get_email(self, msg_id: str) -> Optional[dict]:
        headers = self._headers()
        if not headers:
            return None
        url = f"{GRAPH_BASE}/me/messages/{msg_id}"
        try:
            r = requests.get(url, headers=headers, timeout=30)
            r.raise_for_status()
            return self._parse_message(r.json())
        except Exception as e:
            logger.error(f"Outlook get_email error: {e}")
            return None

    def _parse_message(self, msg: dict) -> dict:
        return {
            "id": msg.get("id"),
            "account": self.account_label,
            "subject": msg.get("subject", "(no subject)"),
            "from": msg.get("from", {}).get("emailAddress", {}).get("address", ""),
            "to": ", ".join(
                r["emailAddress"]["address"]
                for r in msg.get("toRecipients", [])
            ),
            "date": msg.get("receivedDateTime", ""),
            "body": msg.get("body", {}).get("content", ""),
            "labels": [msg.get("inferenceClassification", "")],
            "snippet": msg.get("bodyPreview", ""),
            "is_read": msg.get("isRead", False),
            "conversation_id": msg.get("conversationId"),
        }

    def send_email(self, to: str, subject: str, body: str, reply_to_id: Optional[str] = None) -> bool:
        headers = self._headers()
        if not headers:
            return False
        payload = {
            "message": {
                "subject": subject,
                "body": {"contentType": "Text", "content": body},
                "toRecipients": [{"emailAddress": {"address": to}}],
            }
        }
        if reply_to_id:
            url = f"{GRAPH_BASE}/me/messages/{reply_to_id}/reply"
            payload = {"message": payload["message"], "comment": body}
        else:
            url = f"{GRAPH_BASE}/me/sendMail"
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=30)
            r.raise_for_status()
            logger.info(f"Outlook {self.account_label}: sent email to {to}")
            return True
        except Exception as e:
            logger.error(f"Outlook send error: {e}")
            return False

    def move_to_folder(self, msg_id: str, folder_name: str) -> bool:
        headers = self._headers()
        if not headers:
            return False
        try:
            # Get or create destination folder
            folder_id = self._get_or_create_folder(folder_name, headers)
            url = f"{GRAPH_BASE}/me/messages/{msg_id}/move"
            r = requests.post(url, headers=headers, json={"destinationId": folder_id}, timeout=30)
            r.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Outlook move error: {e}")
            return False

    def _get_or_create_folder(self, name: str, headers: dict) -> str:
        r = requests.get(f"{GRAPH_BASE}/me/mailFolders", headers=headers, timeout=30)
        r.raise_for_status()
        for folder in r.json().get("value", []):
            if folder["displayName"].lower() == name.lower():
                return folder["id"]
        r2 = requests.post(
            f"{GRAPH_BASE}/me/mailFolders",
            headers=headers,
            json={"displayName": name},
            timeout=30,
        )
        r2.raise_for_status()
        return r2.json()["id"]

    def create_calendar_event(self, title: str, start: str, end: str, description: str = "") -> bool:
        headers = self._headers()
        if not headers:
            return False
        payload = {
            "subject": title,
            "body": {"contentType": "Text", "content": description},
            "start": {"dateTime": start, "timeZone": os.getenv("TIMEZONE", "Europe/Paris")},
            "end": {"dateTime": end, "timeZone": os.getenv("TIMEZONE", "Europe/Paris")},
        }
        try:
            r = requests.post(f"{GRAPH_BASE}/me/events", headers=headers, json=payload, timeout=30)
            r.raise_for_status()
            logger.info(f"Outlook calendar event created: {title}")
            return True
        except Exception as e:
            logger.error(f"Outlook calendar error: {e}")
            return False
