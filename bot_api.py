import os
import requests
from fastapi import FastAPI
from pydantic import BaseModel
from supabase import create_client
from dotenv import load_dotenv
import json
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from email.mime.text import MIMEText
import base64
from datetime import datetime

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# ---------- Gmail ----------
token_data = json.loads(os.environ["GOOGLE_TOKEN_JSON"])
credentials = Credentials(
    token=token_data["token"],
    refresh_token=token_data.get("refresh_token"),
    client_id=token_data.get("client_id"),
    client_secret=token_data.get("client_secret"),
    token_uri=token_data.get("token_uri"),
    scopes=["https://www.googleapis.com/auth/gmail.send"],
)
gmail_service = build("gmail", "v1", credentials=credentials)


def send_email(to_email: str, subject: str, body: str):
    message = MIMEText(body,'html')
    message["to"] = to_email
    message["subject"] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    gmail_service.users().messages().send(userId="me", body={"raw": raw}).execute()


# ---------- FastAPI ----------
app = FastAPI()


class MessageRequest(BaseModel):
    email: str
    message: str


@app.post("/send-message")
def send_message(req: MessageRequest):
    user = (
        supabase.table("user")
        .select("chat_id")
        .eq("email", req.email)
        .maybe_single()
        .execute()
    )

    if not user or not user.data:
        return {"error": "Email not registered"}

    chat_id = user.data.get("chat_id")

    if not chat_id:
        return {"error": "User has not connected Telegram"}

    telegram_response = requests.post(
        f"{TELEGRAM_API_URL}/sendMessage",
        json={
            "chat_id": chat_id, 
            "text": req.message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            },
    )

    if telegram_response.status_code != 200:
        return {"error": "Telegram send failed"}

    return {"status": "sent"}



# ---------- Telegram Webhook ----------
@app.post("/webhook")
async def telegram_webhook(update: dict):
    message = update.get("message") or update.get("edited_message")
    if not message:
        return {"status": "no message"}

    chat_id = message["chat"]["id"]
    raw_text = (message.get("text") or "").strip()
    cmd_lower = raw_text.lower()

    # -------- Handle /start --------
    if cmd_lower.startswith("/start"):
        parts = raw_text.split(" ", 1)

        # Check if user is already linked
        try:
            existing_user = (
                supabase.table("user")
                .select("email")
                .eq("chat_id", str(chat_id))
                .maybe_single()
                .execute()
            )

            if existing_user and existing_user.data:
                # User already linked
                requests.post(
                    f"{TELEGRAM_API_URL}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": (
                            f"✅ You're already connected to TAMDAN!\n\n"
                            f"Linked email: {existing_user.data['email']}\n\n"
                            "You'll receive news updates here automatically."
                        ),
                    },
                )
                return {"status": "already_linked"}
        except Exception as e:
            print(f"Error checking existing user: {e}")

        if len(parts) == 2:
            token_or_email = parts[1].strip()

            # Try token-based linking first (automatic from website)
            if len(token_or_email) == 64:  # hex token is 64 chars
                try:
                    # Verify token
                    token_result = (
                        supabase.table("telegram_tokens")
                        .select("*")
                        .eq("token", token_or_email)
                        .eq("used", False)
                        .maybe_single()
                        .execute()
                    )

                    if not token_result or not token_result.data:
                        requests.post(
                            f"{TELEGRAM_API_URL}/sendMessage",
                            json={
                                "chat_id": chat_id,
                                "text": "❌ Invalid or expired link. Please try again from the website.",
                            },
                        )
                        return {"status": "invalid_token"}

                    token_data = token_result.data
                    
                    # Check expiration
                    expires_at = datetime.fromisoformat(token_data["expires_at"].replace("Z", "+00:00"))
                    if datetime.now(expires_at.tzinfo) > expires_at:
                        requests.post(
                            f"{TELEGRAM_API_URL}/sendMessage",
                            json={
                                "chat_id": chat_id,
                                "text": "❌ Link expired. Please generate a new one from the website.",
                            },
                        )
                        return {"status": "token_expired"}

                    email = token_data["email"]

                    # Mark token as used
                    supabase.table("telegram_tokens").update({"used": True}).eq(
                        "token", token_or_email
                    ).execute()

                    # Link chat_id to user
                    requests.post(
                        "https://my-next-app-seven-delta.vercel.app/api/bots/save_chat_id",
                        json={"email": email, "chat_id": chat_id},
                    )

                    requests.post(
                        f"{TELEGRAM_API_URL}/sendMessage",
                        json={
                            "chat_id": chat_id,
                            "text": f"✅ Successfully linked to {email}!\n\nYou'll now receive news updates here.",
                        },
                    )
                    return {"status": "linked_via_token"}

                except Exception as e:
                    print(f"Token verification error: {e}")
                    requests.post(
                        f"{TELEGRAM_API_URL}/sendMessage",
                        json={
                            "chat_id": chat_id,
                            "text": "⚠️ Server error. Please try again.",
                        },
                    )
                    return {"status": "error"}

            # Fallback: manual email linking (old method)
            email = token_or_email
            try:
                check = (
                    supabase.table("user")
                    .select("*")
                    .eq("email", email)
                    .maybe_single()
                    .execute()
                )
            except:
                requests.post(
                    f"{TELEGRAM_API_URL}/sendMessage",
                    json={"chat_id": chat_id, "text": "⚠️ Server error, try again."},
                )
                return {"status": "db_error"}

            if not check or not getattr(check, "data", None):
                requests.post(
                    f"{TELEGRAM_API_URL}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": (
                            f"❌ The email *{email}* is not registered.\n\n"
                            "Please log in first:\n"
                            "https://my-next-app-seven-delta.vercel.app/"
                        ),
                        "parse_mode": "Markdown",
                    },
                )
                return {"status": "email_not_found"}

            # Save chat_id
            requests.post(
                "https://my-next-app-seven-delta.vercel.app/api/bots/save_chat_id",
                json={"email": email, "chat_id": chat_id},
            )

            requests.post(
                f"{TELEGRAM_API_URL}/sendMessage",
                json={"chat_id": chat_id, "text": "✅ Linked successfully!"},
            )
            return {"status": "linked"}

        # No parameter provided - show welcome only if not already linked
        requests.post(
            f"{TELEGRAM_API_URL}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": (
                    "👋 Welcome to TAMDAN!\n\n"
                    "To link your account:\n"
                    "1. Log in at https://my-next-app-seven-delta.vercel.app/\n"
                    "2. Click 'Connect with Telegram' in your profile\n\n"
                    "Or manually send: `/start your_email@example.com`"
                ),
                "parse_mode": "Markdown",
            },
        )
        return {"status": "start_no_param"}

    # -------- Default echo --------
    requests.post(
        f"{TELEGRAM_API_URL}/sendMessage",
        json={"chat_id": chat_id, "text": f"You said: {raw_text}"},
    )

    return {"status": "ok"}


@app.get("/")
async def root():
    return {"message": "Telegram bot is running"}