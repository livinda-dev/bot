# bot_link_user_webhook.py
import os
from fastapi import FastAPI, Request
from pydantic import BaseModel
import requests
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

app = FastAPI()

TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Pydantic model for incoming Telegram update
class TelegramUpdate(BaseModel):
    update_id: int
    message: dict = None
    edited_message: dict = None

def send_message(chat_id: int, text: str):
    """Send a message to Telegram user."""
    url = f"{TELEGRAM_API_URL}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    response = requests.post(url, json=payload)
    return response.json()

@app.post("/webhook")
async def telegram_webhook(update: TelegramUpdate):
    """Handle incoming webhook updates from Telegram."""
    message = update.message or update.edited_message
    if not message:
        return {"status": "no message"}

    chat_id = message["chat"]["id"]
    text = message.get("text", "")

    # Example: handle /start command
    if text == "/start":
        send_message(chat_id, "Hello! Your bot is now linked and active ✅")
    else:
        send_message(chat_id, f"You said: {text}")

    return {"status": "ok"}

@app.get("/")
async def root():
    return {"message": "Telegram webhook bot is running"}
