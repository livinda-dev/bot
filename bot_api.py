# bot_api.py
import os
import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

# Telegram Bot Token
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Supabase config
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

# FastAPI app
app = FastAPI()


@app.get("/")
def read_root():
    return {"message": "Hello from your FastAPI bot!"}

@app.get("/ping")
def ping():
    return {"status": "ok", "message": "pong!"}


class MessageRequest(BaseModel):
    email: str
    message: str

@app.post("/send-message")
def send_message(req: MessageRequest):
    # Look up user by email
    result = supabase.table("user").select("*").eq("email", req.email).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="User not found")

    chat_id = result.data[0].get("chat_id")
    if not chat_id:
        raise HTTPException(status_code=400, detail="User has no linked Telegram")

    # Send message via Telegram HTTP API
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": req.message}

    response = requests.post(url, data=payload)

    if response.status_code != 200:
        raise HTTPException(status_code=500, detail=f"Telegram API error: {response.text}")

    return {"status": "success", "chat_id": chat_id, "telegram_response": response.json()}
