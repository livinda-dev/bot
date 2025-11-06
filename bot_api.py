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
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from email.mime.text import MIMEText
import base64

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
# Google Email Setup (OAuth token via ENV)
# ------------------------------
token_data = json.loads(os.environ["GOOGLE_TOKEN_JSON"])
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

credentials = Credentials(
    token=token_data["token"],
    refresh_token=token_data.get("refresh_token"),
    client_id=token_data.get("client_id"),
    client_secret=token_data.get("client_secret"),
    token_uri=token_data.get("token_uri"),
    scopes=SCOPES
)
gmail_service = build("gmail", "v1", credentials=credentials)

def send_email(to_email: str, subject: str, body: str):
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

def request_contact(chat_id: int, text: str):
    """Send a button to request user's contact."""
    payload = {
        "chat_id": chat_id,
        "text": text,
        "reply_markup": {
            "keyboard": [[{"text": "Share my contact", "request_contact": True}]],
            "one_time_keyboard": True,
            "resize_keyboard": True
        }
    }
    requests.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload)

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
    
    send_email(req.email, "New Message from Bot", req.message)
    return {"status": "success", "chat_id": chat_id, "telegram_response": telegram_resp}

@app.post("/webhook")
async def telegram_webhook(update: TelegramUpdate):
    cleanup_expired_requests()
    
    message = update.message or update.edited_message
    if not message:
        return {"status": "no message"}

    chat_id = message["chat"]["id"]
    text = message.get("text", "")
    contact = message.get("contact", {})

    # ------------------------------
    # Step 1: Handle /start linking
    # ------------------------------
    if text.startswith("/start"):
        parts = text.split(" ")
        if len(parts) != 2:
            send_telegram_message(chat_id, "Please link your account by typing: /start your_email@example.com")
            return {"status": "waiting for email"}

        email = parts[1].strip()
        result = supabase.table("user").select("*").eq("email", email).execute()
        if not result.data:
            send_telegram_message(chat_id, "Email not registered. Please register here: https://my-next-hgkfl4ycg-livindas-projects.vercel.app")
            return {"status": "email not found"}

        user = result.data[0]
        current_chat_id = user.get("chat_id")

        if current_chat_id and current_chat_id != chat_id:
            # Ask for unlink confirmation
            supabase.table("user_link_requests").insert({
                "email": email,
                "new_chat_id": chat_id,
                "status": "confirm_unlink",
                "created_at": datetime.utcnow().isoformat()
            }).execute()
            send_telegram_message(chat_id, "This email is already linked with another Telegram account. Do you want to unlink it? (Yes/No)")
            return {"status": "waiting unlink confirmation"}

        # Normal linking (first time)
        supabase.table("user").update({"chat_id": chat_id}).eq("email", email).execute()
        request_contact(chat_id, f"Hello {email}! Your bot is now linked ✅\nPlease share your contact to complete setup.")
        return {"status": "linked, awaiting contact"}

    # ------------------------------
    # Step 2: Handle Yes/No unlink confirmation
    # ------------------------------
    pending_request = supabase.table("user_link_requests").select("*").eq("new_chat_id", chat_id).eq("status", "confirm_unlink").execute()
    if pending_request.data:
        req = pending_request.data[0]
        email = req["email"]
        if text.lower() == "yes":
            otp = generate_otp()
            supabase.table("user_link_requests").update({
                "otp": otp,
                "status": "pending",
                "created_at": datetime.utcnow().isoformat()
            }).eq("id", req["id"]).execute()
            send_email(email, "Your OTP Code", f"Use this OTP to confirm unlinking and linking your Telegram: {otp}")
            send_telegram_message(chat_id, "OTP sent to your email. Please enter the OTP to continue.")
            return {"status": "otp sent"}
        elif text.lower() == "no":
            supabase.table("user_link_requests").delete().eq("id", req["id"]).execute()
            send_telegram_message(chat_id, "Unlink request cancelled.")
            return {"status": "unlink cancelled"}
        else:
            send_telegram_message(chat_id, "Please reply Yes or No.")
            return {"status": "waiting unlink confirmation"}

    # ------------------------------
    # Step 3: Handle OTP verification
    # ------------------------------
    otp_request = supabase.table("user_link_requests").select("*").eq("otp", text).eq("status", "pending").execute()
    if otp_request.data:
        req = otp_request.data[0]
        supabase.table("user_link_requests").update({"status": "await_contact"}).eq("id", req["id"]).execute()
        request_contact(chat_id, "OTP verified ✅\nPlease share your contact to complete linking.")
        return {"status": "awaiting contact"}

    # ------------------------------
    # Step 4: Handle contact sharing
    # ------------------------------
    contact_request = supabase.table("user_link_requests").select("*").eq("status", "await_contact").eq("new_chat_id", chat_id).execute()
    if contact_request.data and contact.get("phone_number"):
        req = contact_request.data[0]
        email = req["email"]
        phone_number = contact["phone_number"]

        supabase.table("user").update({
            "chat_id": chat_id,
            "phone_number": phone_number
        }).eq("email", email).execute()

        supabase.table("user_link_requests").delete().eq("id", req["id"]).execute()

        msg = f"Your contact has been updated successfully ✅\nEmail: {email}\nPhone: {phone_number}"
        send_telegram_message(chat_id, msg)
        send_email(email, "Telegram Linked & Contact Updated", msg)
        return {"status": "contact updated"}

    # ------------------------------
    # Default reply
    # ------------------------------
    send_telegram_message(chat_id, f"You said: {text}")
    return {"status": "ok"}

@app.get("/")
async def root():
    return {"message": "Telegram bot + Supabase API is running"}
