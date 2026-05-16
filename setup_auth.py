"""
Interactive setup script for first-time OAuth2 authentication.
Run this once before starting the main agent.
"""
import os
import sys
from dotenv import load_dotenv

load_dotenv()


def setup_gmail():
    print("\n=== Gmail / Google Calendar OAuth2 ===")
    print("This will open a browser window for authentication.")
    from src.email.gmail_client import GmailClient
    from src.calendar.google_calendar import GoogleCalendarClient
    client = GmailClient()
    print("✓ Gmail authenticated successfully")
    cal = GoogleCalendarClient()
    print("✓ Google Calendar authenticated successfully")


def setup_outlook(account_num: int):
    print(f"\n=== Outlook Account {account_num} OAuth2 ===")
    from src.email.outlook_client import OutlookClient
    client = OutlookClient(account_num)

    print(f"Visit this URL to authorize Outlook account {account_num}:")
    url = client.get_auth_url()
    print(f"\n{url}\n")

    redirect = input("Paste the full redirect URL after authorization: ").strip()
    if client.complete_auth(redirect):
        print(f"✓ Outlook account {account_num} authenticated successfully")
    else:
        print(f"✗ Authentication failed for Outlook account {account_num}")
        sys.exit(1)


def main():
    print("=== Email AI Agent — First-time Setup ===\n")
    print("This script will guide you through authenticating your email accounts.\n")

    accounts = []
    if input("Set up Gmail account? [Y/n] ").strip().lower() != "n":
        accounts.append("gmail")
    if input("Set up Outlook account 1? [Y/n] ").strip().lower() != "n":
        accounts.append("outlook1")
    if input("Set up Outlook account 2? [Y/n] ").strip().lower() != "n":
        accounts.append("outlook2")

    for account in accounts:
        if account == "gmail":
            setup_gmail()
        elif account == "outlook1":
            setup_outlook(1)
        elif account == "outlook2":
            setup_outlook(2)

    print("\n✅ All accounts configured. You can now run: python main.py")


if __name__ == "__main__":
    main()
