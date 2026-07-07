"""
tools/telethon_login.py — ONE-TIME interactive login to generate a Telegram session string.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SETUP REQUIREMENTS (do this once before running):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Get TELEGRAM_API_ID and TELEGRAM_API_HASH from https://my.telegram.org
   - Sign in with your personal Telegram phone number
   - Click "API development tools"
   - Create an application (any name/platform is fine)
   - Copy the "App api_id" (integer) and "App api_hash" (hex string)

2. Add them to .env BEFORE running this script:
       TELEGRAM_API_ID=12345678
       TELEGRAM_API_HASH=abcdef1234567890abcdef1234567890

3. Run this script ONCE, interactively (on your local machine, NOT EC2):
       python tools/telethon_login.py

4. Enter your phone number (international format, e.g. +919876543210)
   and the OTP code Telegram sends to your Telegram app.

5. This script prints the session string and offers to auto-append it to .env:
       TELEGRAM_SESSION_STRING=1BQANOTEuA...

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
AFTER THIS ONE-TIME SETUP:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

All future runs (including on EC2) are fully headless — no interactive
prompt, no session file to manage. The StringSession in .env handles
authentication entirely in memory. Safe across EC2 reboots.

DO NOT run this script on EC2 — it requires interactive input. Run it
locally, copy the session string to your EC2 .env, and you're done.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SECURITY NOTE:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

The session string is equivalent to your Telegram login credentials.
Keep it secret — treat it like a password. It is already in .gitignore
via the .env entry. Never commit .env to git.
"""

import os
import sys

# Ensure we can import from project root when running directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    # ── Validate required env vars ────────────────────────────────────────────
    api_id_str  = os.getenv("TELEGRAM_API_ID", "").strip()
    api_hash    = os.getenv("TELEGRAM_API_HASH", "").strip()

    if not api_id_str or not api_hash:
        print("\n[ERROR] TELEGRAM_API_ID and TELEGRAM_API_HASH must be set in .env")
        print("        Get them from: https://my.telegram.org → API development tools")
        sys.exit(1)

    try:
        api_id = int(api_id_str)
    except ValueError:
        print(f"\n[ERROR] TELEGRAM_API_ID must be an integer, got: {api_id_str!r}")
        sys.exit(1)

    # ── Import Telethon (late import — fail early if not installed) ───────────
    try:
        from telethon.sync import TelegramClient
        from telethon.sessions import StringSession
    except ImportError:
        print("\n[ERROR] telethon is not installed. Run: pip install telethon")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("  Telethon — One-Time Session String Generator")
    print("=" * 60)
    print("  This will log into your personal Telegram account.")
    print("  You will be prompted for your phone number and OTP.")
    print("  Run this ONCE locally. Never run on EC2.")
    print("=" * 60 + "\n")

    # ── Interactive login with empty StringSession (forces new auth) ──────────
    # StringSession("") = fresh session, no existing credentials
    with TelegramClient(StringSession(), api_id, api_hash) as client:
        # client.start() handles the interactive phone+OTP flow automatically
        # It prompts for phone number and OTP code via stdin
        client.start()

        # Extract the session string — this encodes the auth key + server details
        session_string = client.session.save()

    # ── Display the session string ────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  ✅ Login successful! Session string generated:")
    print("=" * 60)
    print(f"\nTELEGRAM_SESSION_STRING={session_string}\n")
    print("=" * 60)

    # ── Offer to auto-append to .env ─────────────────────────────────────────
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")

    # Check if it already exists in .env
    existing_content = ""
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            existing_content = f.read()

    if "TELEGRAM_SESSION_STRING" in existing_content:
        print("  ⚠️  TELEGRAM_SESSION_STRING already exists in .env")
        print("     Manually update it with the new session string above.")
    else:
        answer = input("  Append TELEGRAM_SESSION_STRING to .env automatically? [y/N]: ").strip().lower()
        if answer == "y":
            with open(env_path, "a") as f:
                # Ensure there's a newline before appending
                if existing_content and not existing_content.endswith("\n"):
                    f.write("\n")
                f.write(f"TELEGRAM_SESSION_STRING={session_string}\n")
            print(f"  ✅ Appended to {env_path}")
        else:
            print("  ℹ️  Copy the line above manually into your .env file.")

    print("\n  Done! You can now run sources/telegram_channels.py headlessly.")
    print("  On EC2: copy TELEGRAM_SESSION_STRING into your .env there too.\n")


if __name__ == "__main__":
    main()
