# bot_api.py
import os
import requests
import random
import string
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from supabase import create_client
from dotenv import load_dotenv

import json
from google.oauth2 import service_account
from googleapiclient.discovery import build

load_dotenv()

# ------------------------------
# Config
# ------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# ------------------------------
# Google Email Setup
# ------------------------------
# The path to the secret file on Render
GOOGLE_TOKEN_JSON_PATH = "/etc/secrets/token.json"

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
# Load credentials directly from the secret file
credentials = service_account.Credentials.from_service_account_file(
    GOOGLE_TOKEN_JSON_PATH, scopes=SCOPES
)
gmail_service = build("gmail", "v1", credentials=credentials)

def send_email(to_email: str, subject: str, body: str):
    from email.mime.text import MIMEText
    import base64

    message = MIMEText(body)
    message["to"] = to_email
    message["subject"] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    gmail_service.users().messages().send(userId="me", body={"raw": raw}).execute()

# ------------------------------
# FastAPI App
# ------------------------------
app = FastAPI(title="Telegram Bot + Supabase API")

# ------------------------------
# Models
# ------------------------------
class MessageRequest(BaseModel):
    email: str
    message: str

class TelegramUpdate(BaseModel):
    update_id: int
    message: dict = None
    edited_message: dict = None

# ------------------------------
# Helper Functions
# ------------------------------
def send_telegram_message(chat_id: int, text: str):
    payload = {"chat_id": chat_id, "text": text}
    response = requests.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload)
    return response.json()

def generate_otp(length=6):
    return ''.join(random.choices(string.digits, k=length))

def cleanup_expired_requests():
    """Delete user_link_requests older than 24h"""
    cutoff = datetime.utcnow() - timedelta(hours=24)
    supabase.table("user_link_requests").delete().lt("created_at", cutoff.isoformat()).execute()

# ------------------------------
# Routes
# ------------------------------
@app.post("/send-message")
def send_message(req: MessageRequest):
    result = supabase.table("user").select("*").eq("email", req.email).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="User not found")
    
    user = result.data[0]
    chat_id = user.get("chat_id")
    telegram_resp = None
    if chat_id:
        telegram_resp = send_telegram_message(chat_id, req.message)
    
    # Optional: send email as well
    send_email(req.email, "New Message from Bot", req.message)
    
    return {"status": "success", "chat_id": chat_id, "telegram_response": telegram_resp}

@app.post("/webhook")
async def telegram_webhook(update: TelegramUpdate):
    cleanup_expired_requests()  # remove old OTP requests
    
    message = update.message or update.edited_message
    if not message:
        return {"status": "no message"}

    chat_id = message["chat"]["id"]
    text = message.get("text", "")

    # ------------------------------
    # Handle /start linking
    # ------------------------------
    if text.startswith("/start"):
        parts = text.split(" ")
        if len(parts) != 2:
            send_telegram_message(chat_id, "Please link your account by typing: /start your_email@example.com")
            return {"status": "waiting for email"}

        email = parts[1]
        result = supabase.table("user").select("*").eq("email", email).execute()
        if not result.data:
            # Email not exist → send registration URL
            send_telegram_message(chat_id, "Email not registered. Please register here: https://my-next-hgkfl4ycg-livindas-projects.vercel.app")
            return {"status": "email not found"}

        user = result.data[0]
        current_chat_id = user.get("chat_id")
        if current_chat_id and current_chat_id != chat_id:
            # Already linked to another Telegram account → ask to confirm change
            otp = generate_otp()
            supabase.table("user_link_requests").insert({
                "email": email,
                "new_chat_id": chat_id,
                "otp": otp,
                "status": "pending",
                "created_at": datetime.utcnow().isoformat()
            }).execute()
            send_email(email, "Your OTP Code", f"Use this OTP to confirm linking your Telegram: {otp}")
            send_telegram_message(chat_id, "This email is already linked with another Telegram account. Do you want to update it? Please enter the OTP sent to your email.")
            return {"status": "otp sent"}

        # Normal linking
        supabase.table("user").update({"chat_id": chat_id}).eq("email", email).execute()
        send_telegram_message(chat_id, f"Hello {email}! Your bot is now linked and active ✅")
        return {"status": "linked"}

    # ------------------------------
    # Handle OTP verification
    # ------------------------------
    result = supabase.table("user_link_requests").select("*").eq("otp", text).eq("status", "pending").execute()
    if result.data:
        req = result.data[0]
        supabase.table("user").update({"chat_id": req["new_chat_id"]}).eq("email", req["email"]).execute()
        supabase.table("user_link_requests").update({"status": "completed"}).eq("id", req["id"]).execute()
        send_telegram_message(chat_id, f"Your Telegram has been linked to {req['email']} successfully ✅")
        send_email(req["email"], "Telegram Linked", f"Your Telegram account has been linked to your email {req['email']}.")
        return {"status": "otp verified"}

    # ------------------------------
    # Default reply
    # ------------------------------
    send_telegram_message(chat_id, f"You said: {text}")
    return {"status": "ok"}

@app.get("/")
async def root():
    return {"message": "Telegram bot + Supabase API is running"}
