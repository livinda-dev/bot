# bot_service.py
import os
import requests
from fastapi import FastAPI, HTTPException, Request
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
    """Send a message via Telegram."""
    payload = {"chat_id": chat_id, "text": text}
    response = requests.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload)
    return response.json()

# ------------------------------
# Routes
# ------------------------------
@app.post("/send-message")
def send_message(req: MessageRequest):
    # Look up user by email in Supabase
    result = supabase.table("user").select("*").eq("email", req.email).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="User not found")

    chat_id = result.data[0].get("chat_id")
    # Send Telegram message if chat_id exists
    telegram_resp = None
    if chat_id:
        telegram_resp = send_telegram_message(chat_id, req.message)

    # TODO: Here you can also send email if you want
    return {
        "status": "success",
        "chat_id": chat_id,
        "telegram_response": telegram_resp
    }

@app.post("/webhook")
async def telegram_webhook(update: TelegramUpdate):
    """Handle incoming webhook updates from Telegram."""
    message = update.message or update.edited_message
    if not message:
        return {"status": "no message"}

    chat_id = message["chat"]["id"]
    text = message.get("text", "")

    if text == "/start":
        send_telegram_message(chat_id, "Hello! Your bot is now linked and active ✅")
    else:
        send_telegram_message(chat_id, f"You said: {text}")

    return {"status": "ok"}

@app.get("/")
async def root():
    return {"message": "Telegram bot + Supabase API is running"}
