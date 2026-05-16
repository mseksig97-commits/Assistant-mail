import os
from datetime import datetime
from typing import Optional
from dateutil import parser as dateparser

from src.email.gmail_client import GmailClient
from src.email.outlook_client import OutlookClient
from src.calendar.google_calendar import GoogleCalendarClient
from src.agent.ai_processor import AIProcessor
from src.utils.logger import setup_logger

logger = setup_logger("email_manager")

CATEGORY_LABEL_MAP = {
    "urgent": "⚡ Urgent",
    "important": "⭐ Important",
    "work": "💼 Travail",
    "personal": "👤 Personnel",
    "finance": "💰 Finance",
    "newsletter": "📰 Newsletter",
    "spam": "🗑️ Spam",
    "social": "🌐 Social",
    "information": "ℹ️ Info",
    "other": "📁 Autre",
}


class EmailManager:
    def __init__(self):
        self.ai = AIProcessor()
        self.gmail = GmailClient()
        self.outlook1 = OutlookClient(1)
        self.outlook2 = OutlookClient(2)
        self.gcal = GoogleCalendarClient()

        # Use Outlook account 1 for calendar by default (configurable)
        cal_account = int(os.getenv("OUTLOOK_CALENDAR_ACCOUNT", "1"))
        self.outlook_cal = self.outlook1 if cal_account == 1 else self.outlook2

    def _all_clients(self):
        return [self.gmail, self.outlook1, self.outlook2]

    # ─── Email fetching ───────────────────────────────────────────────────────

    def fetch_all_emails(self, max_per_account: int = 30) -> list[dict]:
        emails = []
        emails += self.gmail.list_emails(max_results=max_per_account)
        emails += self.outlook1.list_emails(max_results=max_per_account)
        emails += self.outlook2.list_emails(max_results=max_per_account)
        logger.info(f"Fetched {len(emails)} emails total")
        return emails

    # ─── Analysis & sorting ──────────────────────────────────────────────────

    def analyze_and_sort(self, max_per_account: int = 30) -> list[dict]:
        """Fetch, analyze and label all emails. Returns enriched list."""
        emails = self.fetch_all_emails(max_per_account)
        results = []
        for email in emails:
            analysis = self.ai.analyze_email(email)
            enriched = {**email, **analysis}
            self._apply_label_to_email(enriched)
            self._handle_calendar_events(enriched)
            results.append(enriched)
            logger.info(
                f"[{email['account']}] '{email['subject'][:50]}' → "
                f"{analysis.get('category')} (importance={analysis.get('importance')})"
            )
        return results

    def _apply_label_to_email(self, email: dict):
        cat = email.get("category", "other")
        label = CATEGORY_LABEL_MAP.get(cat, "📁 Autre")
        account = email.get("account", "")
        try:
            if account == "gmail":
                self.gmail.apply_label(email["id"], label)
            elif account == "outlook1":
                self.outlook1.move_to_folder(email["id"], label)
            elif account == "outlook2":
                self.outlook2.move_to_folder(email["id"], label)
        except Exception as e:
            logger.warning(f"Label apply failed for {email.get('id')}: {e}")

    def _handle_calendar_events(self, email: dict):
        for event in email.get("events", []):
            try:
                start_str = event.get("date", "")
                if not start_str:
                    continue
                all_day = event.get("all_day", False)
                start_dt = dateparser.parse(start_str)
                end_dt = dateparser.parse(event["end_date"]) if event.get("end_date") else None

                title = event.get("title", email.get("subject", "Événement"))
                desc = f"Extrait de l'email : {email.get('subject')}\nDe : {email.get('from')}\n\n{event.get('description', '')}"

                self.gcal.create_event(title, start_dt, end_dt, desc, all_day)
                logger.info(f"Calendar event created: {title}")
            except Exception as e:
                logger.warning(f"Calendar event creation failed: {e}")

    # ─── Reply drafting ───────────────────────────────────────────────────────

    def draft_reply(self, email: dict, instructions: str = "") -> str:
        return self.ai.draft_reply(email, instructions)

    def send_reply(self, email: dict, body: str) -> bool:
        account = email.get("account", "")
        to = email.get("from", "")
        subject = "Re: " + email.get("subject", "")
        msg_id = email.get("id")

        if account == "gmail":
            return self.gmail.send_email(to, subject, body, reply_to_id=msg_id)
        elif account == "outlook1":
            return self.outlook1.send_email(to, subject, body, reply_to_id=msg_id)
        elif account == "outlook2":
            return self.outlook2.send_email(to, subject, body, reply_to_id=msg_id)
        return False

    # ─── Daily summary ───────────────────────────────────────────────────────

    def generate_daily_summary(self) -> str:
        emails = self.fetch_all_emails(max_per_account=50)
        analyzed = []
        for email in emails:
            analysis = self.ai.analyze_email(email)
            analyzed.append({
                "account": email.get("account"),
                "subject": email.get("subject"),
                "from": email.get("from"),
                "date": email.get("date"),
                **analysis,
            })
        return self.ai.generate_daily_summary(analyzed)

    # ─── Search ──────────────────────────────────────────────────────────────

    def search_emails(self, query: str, max_results: int = 20) -> list[dict]:
        results = self.gmail.list_emails(max_results=max_results, query=query)
        q = query.lower()
        for client in [self.outlook1, self.outlook2]:
            for email in client.list_emails(max_results=100):
                text = f"{email['subject']} {email['from']} {email.get('body','')} {email.get('snippet','')}".lower()
                if q in text:
                    results.append(email)
        return results

    # ─── Chat passthrough ─────────────────────────────────────────────────────

    def chat(self, message: str, context: str = "") -> str:
        return self.ai.chat(message, context)
