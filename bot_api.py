# bot_api.py
import os
import random
import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from supabase import create_client
from dotenv import load_dotenv

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
# FastAPI App
# ------------------------------
app = FastAPI(title="Telegram Bot + Supabase API")

# ------------------------------
# Pydantic Models
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
    return requests.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload).json()

def generate_otp():
    return str(random.randint(100000, 999999))

def link_telegram_to_user(chat_id: int, email: str):
    supabase.table("user").update({"chat_id": chat_id}).eq("email", email).execute()

def store_otp(email: str, otp: str):
    """Save OTP in Supabase or in-memory table."""
    supabase.table("otp").upsert({"email": email, "otp": otp}).execute()

def verify_otp(email: str, otp: str) -> bool:
    result = supabase.table("otp").select("*").eq("email", email).eq("otp", otp).execute()
    return bool(result.data)

# ------------------------------
# Routes
# ------------------------------
@app.post("/send-message")
def send_message(req: MessageRequest):
    """Send message via Telegram (and email if needed)."""
    result = supabase.table("user").select("*").eq("email", req.email).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="User not found")

    user = result.data[0]
    chat_id = user.get("chat_id")

    telegram_resp = None
    if chat_id:
        telegram_resp = send_telegram_message(chat_id, req.message)

    # TODO: add email sending via Google API here

    return {"status": "success", "chat_id": chat_id, "telegram_response": telegram_resp}

@app.post("/webhook")
async def telegram_webhook(update: TelegramUpdate):
    """Handle Telegram updates and maintain multi-step /start flow."""
    message = update.message or update.edited_message
    if not message:
        return {"status": "no message"}

    chat_id = message["chat"]["id"]
    text = message.get("text", "")

    if text.startswith("/start"):
        parts = text.split(" ")
        if len(parts) == 2:
            email = parts[1]
            # Check if email exists
            result = supabase.table("user").select("*").eq("email", email).execute()
            if not result.data:
                send_telegram_message(chat_id, "Email not found. Please register first.")
                return {"status": "email not found"}

            user = result.data[0]
            if user.get("chat_id"):
                send_telegram_message(chat_id, "This email is already linked with another Telegram account.")
                return {"status": "already linked"}

            otp = generate_otp()
            store_otp(email, otp)
            send_telegram_message(chat_id, f"Your OTP is: {otp}. Please reply with /verify {email} <OTP>")
        else:
            send_telegram_message(chat_id, "Please link your account by typing: /start your_email@example.com")

    elif text.startswith("/verify"):
        parts = text.split(" ")
        if len(parts) == 3:
            email, otp = parts[1], parts[2]
            if verify_otp(email, otp):
                link_telegram_to_user(chat_id, email)
                send_telegram_message(chat_id, f"✅ Email {email} successfully linked to Telegram!")
            else:
                send_telegram_message(chat_id, "❌ Invalid OTP. Please try again.")
        else:
            send_telegram_message(chat_id, "Usage: /verify your_email@example.com <OTP>")

    else:
        send_telegram_message(chat_id, f"You said: {text}")

    return {"status": "ok"}

@app.get("/")
async def root():
    return {"message": "Telegram bot + Supabase API is running"}
