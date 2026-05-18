import os
import json
import requests
from typing import Optional

import msal

from src.utils.logger import setup_logger

logger = setup_logger("outlook_client")

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
SCOPES = [
    "Mail.ReadWrite",
    "Mail.Send",
    "Calendars.ReadWrite",
]


class OutlookClient:
    def __init__(self, account_num: int):
        prefix = f"OUTLOOK{account_num}_"
        self.client_id = os.getenv(f"{prefix}CLIENT_ID")
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
        # Public client — no client secret (required for personal Microsoft accounts)
        self._app = msal.PublicClientApplication(
            self.client_id,
            authority=f"https://login.microsoftonline.com/{self.tenant_id}",
            token_cache=self._token_cache,
        )

    def initiate_device_flow(self) -> dict:
        """Start device-code flow. Returns the flow dict (contains user_code and verification_uri)."""
        flow = self._app.initiate_device_flow(scopes=SCOPES)
        os.makedirs(os.path.dirname(self.token_file), exist_ok=True)
        with open(self.token_file + ".flow", "w") as f:
            json.dump(flow, f)
        return flow

    def complete_device_flow(self) -> bool:
        """Block until the user completes device authorization, then save the token."""
        flow_file = self.token_file + ".flow"
        if not os.path.exists(flow_file):
            logger.error("No pending device flow found")
            return False
        with open(flow_file) as f:
            flow = json.load(f)
        result = self._app.acquire_token_by_device_flow(flow)
        if "access_token" in result:
            self._save_token_cache()
            os.remove(flow_file)
            logger.info(f"Outlook {self.account_label} authenticated via device flow")
            return True
        logger.error(f"Outlook device flow error: {result.get('error_description')}")
        return False

    def _get_token(self) -> Optional[str]:
        accounts = self._app.get_accounts()
        result = None
        if accounts:
            result = self._app.acquire_token_silent(SCOPES, account=accounts[0])
        if not result or "access_token" not in result:
            logger.warning(f"Outlook {self.account_label}: token missing or expired, re-auth needed")
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
            return [self._parse_message(m) for m in r.json().get("value", [])]
        except Exception as e:
            logger.error(f"Outlook list error: {e}")
            return []

    def get_email(self, msg_id: str) -> Optional[dict]:
        headers = self._headers()
        if not headers:
            return None
        try:
            r = requests.get(f"{GRAPH_BASE}/me/messages/{msg_id}", headers=headers, timeout=30)
            r.raise_for_status()
            return self._parse_message(r.json())
        except Exception as e:
            logger.error(f"Outlook get_email error: {e}")
            return None

    def _parse_message(self, msg: dict) -> dict:
        # Extract List-Unsubscribe from internet message headers if present
        inet_headers = {h["name"]: h["value"] for h in msg.get("internetMessageHeaders", [])}
        return {
            "id": msg.get("id"),
            "account": self.account_label,
            "subject": msg.get("subject", "(no subject)"),
            "from": msg.get("from", {}).get("emailAddress", {}).get("address", ""),
            "to": ", ".join(r["emailAddress"]["address"] for r in msg.get("toRecipients", [])),
            "date": msg.get("receivedDateTime", ""),
            "body": msg.get("body", {}).get("content", ""),
            "labels": [msg.get("inferenceClassification", "")],
            "snippet": msg.get("bodyPreview", ""),
            "list_unsubscribe": inet_headers.get("List-Unsubscribe", ""),
            "is_read": msg.get("isRead", False),
            "conversation_id": msg.get("conversationId"),
        }

    def send_email(self, to: str, subject: str, body: str, reply_to_id: Optional[str] = None) -> bool:
        headers = self._headers()
        if not headers:
            return False
        if reply_to_id:
            url = f"{GRAPH_BASE}/me/messages/{reply_to_id}/reply"
            payload = {"comment": body}
        else:
            url = f"{GRAPH_BASE}/me/sendMail"
            payload = {
                "message": {
                    "subject": subject,
                    "body": {"contentType": "Text", "content": body},
                    "toRecipients": [{"emailAddress": {"address": to}}],
                }
            }
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=30)
            r.raise_for_status()
            logger.info(f"Outlook {self.account_label}: sent to {to}")
            return True
        except Exception as e:
            logger.error(f"Outlook send error: {e}")
            return False

    def move_to_folder(self, msg_id: str, folder_name: str) -> bool:
        headers = self._headers()
        if not headers:
            return False
        try:
            folder_id = self._get_or_create_folder(folder_name, headers)
            r = requests.post(
                f"{GRAPH_BASE}/me/messages/{msg_id}/move",
                headers=headers,
                json={"destinationId": folder_id},
                timeout=30,
            )
            r.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Outlook move error: {e}")
            return False

    def _get_or_create_folder(self, name: str, headers: dict) -> str:
        try:
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
        except Exception as e:
            logger.error(f"Outlook _get_or_create_folder error: {e}")
            raise

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
