"""
One-time setup script — run locally to get your Google OAuth refresh token.

Prerequisites:
  1. Go to https://console.cloud.google.com/
  2. Create a project (or reuse one).
  3. Enable these APIs:
       - Gmail API
       - Google Calendar API
       - Google Drive API
  4. Create OAuth 2.0 credentials → Desktop app.
  5. Download the JSON and save it as  client_secrets.json  in this directory.
  6. Run:  python setup_google_token.py

The script opens a browser tab. Sign in with shuning.wang@shopee.com and grant access.
It then prints the three values you need to paste into Vercel's environment variables.
"""

import json
import sys

try:
    from google_auth_oauthlib.flow import InstalledAppFlow
except ImportError:
    print("Run:  pip install google-auth-oauthlib")
    sys.exit(1)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

SECRETS_FILE = "client_secrets.json"

try:
    with open(SECRETS_FILE) as f:
        secrets = json.load(f)
except FileNotFoundError:
    print(f"ERROR: {SECRETS_FILE} not found.")
    print("Download it from Google Cloud Console → APIs & Services → Credentials.")
    sys.exit(1)

print("Opening browser for Google OAuth…")
flow = InstalledAppFlow.from_client_secrets_file(SECRETS_FILE, SCOPES)
creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")

print("\n" + "=" * 60)
print("SUCCESS! Add these to your Vercel environment variables:")
print("=" * 60)
print(f"\nGOOGLE_CLIENT_ID     = {creds.client_id}")
print(f"GOOGLE_CLIENT_SECRET = {creds.client_secret}")
print(f"GOOGLE_REFRESH_TOKEN = {creds.refresh_token}")
print("\n(Keep these secret — never commit them to git.)")
