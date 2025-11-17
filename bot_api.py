# bot_api.py – FIXED VERSION (email case preserved, stable linking)

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
    message = MIMEText(body)
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
    result = (
        supabase.table("user")
        .select("*")
        .eq("email", req.email)
        .single()
        .execute()
    )

    if not result.data:
        return {"error": "Email not found"}

    user = result.data
    chat_id = user.get("chat_id")

    if chat_id:
        requests.post(
            f"{TELEGRAM_API_URL}/sendMessage",
            json={"chat_id": chat_id, "text": req.message},
        )

    send_email(req.email, "New Message", req.message)
    return {"status": "sent"}


# ---------- Telegram Webhook ----------
@app.post("/webhook")
async def telegram_webhook(update: dict):
    message = update.get("message") or update.get("edited_message")
    if not message:
        return {"status": "no message"}

    chat_id = message["chat"]["id"]

    # IMPORTANT: do NOT lowercase the email!
    raw_text = (message.get("text") or "").strip()
    cmd_lower = raw_text.lower()

    # -------- Handle /start --------
    if cmd_lower.startswith("/start"):
        parts = raw_text.split(" ", 1)

        # -------------------------------
        # CASE 1: /start email
        # -------------------------------
        if len(parts) == 2:
            email = parts[1].strip()

            try:
                check = (
                    supabase.table("user")
                    .select("*")
                    .eq("email", email)  # exact match
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

        # -------------------------------
        # CASE 2: bare /start (desktop)
        # -------------------------------
        requests.post(
            f"{TELEGRAM_API_URL}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": (
                    "👋 Welcome to TAMDAN!\n\n"
                    "To link your account, please send your email:\n"
                    "`/start your_email@example.com`"
                ),
                "parse_mode": "Markdown",
            },
        )
        return {"status": "start_no_email"}

    # -------- Default echo --------
    requests.post(
        f"{TELEGRAM_API_URL}/sendMessage",
        json={"chat_id": chat_id, "text": f"You said: {raw_text}"},
    )

    return {"status": "ok"}


@app.get("/")
async def root():
    return {"message": "Telegram bot is running"}
