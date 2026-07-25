import os
import requests
from fastapi import FastAPI
from pydantic import BaseModel
from supabase import create_client
from dotenv import load_dotenv
import json
from datetime import datetime
import re
from bs4 import BeautifulSoup

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# ---------- FastAPI ----------
app = FastAPI()


class MessageRequest(BaseModel):
    phone_number: str
    message: str


def convert_html_to_telegram(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    # Remove unsupported tags entirely
    for tag in soup(["img", "style", "script"]):
        tag.decompose()

    # Convert headings to bold text
    for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        tag.replace_with(f"<b>{tag.get_text(strip=True)}</b>\n")

    # Convert list items to bullet points
    for li in soup.find_all("li"):
        text = li.get_text(strip=True)
        li.replace_with(f"• {text}\n")

    # Simplify links to Telegram-supported format
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        href = a["href"]
        a.replace_with(f'<a href="{href}">{text}</a>')

    # Remove all inline styles + attributes
    for tag in soup.find_all():
        tag.attrs = {}

    # Replace <div> and <p> with line breaks
    return re.sub(r'\n\s*\n+', '\n\n', soup.get_text(separator="\n").strip())


MAX_LENGTH = 4096

def split_into_chunks(text, max_len=MAX_LENGTH):
    chunks = []
    while len(text) > max_len:
        split_pos = text.rfind("\n", 0, max_len)
        if split_pos == -1:
            split_pos = max_len
        chunks.append(text[:split_pos])
        text = text[split_pos:].lstrip()
    if text:
        chunks.append(text)
    return chunks


@app.post("/send-message")
def send_message(req: MessageRequest):
    user = (
        supabase.table("users")
        .select("chat_id")
        .eq("phone_number", req.phone_number)
        .maybe_single()
        .execute()
    )

    if not user or not user.data:
        return {"error": "Phone number not registered"}

    chat_id = user.data.get("chat_id")

    if not chat_id:
        return {"error": "User has not connected Telegram"}

    telegram_text = convert_html_to_telegram(req.message)
    chunks = split_into_chunks(telegram_text)

    for i, chunk in enumerate(chunks, start=1):
        resp = requests.post(
            f"{TELEGRAM_API_URL}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
        )
        if resp.status_code != 200:
            print("---- TELEGRAM ERROR ----")
            print("Status:", resp.status_code)
            print("Response:", resp.text)
            print("-----------------------")
            return {"error": "Telegram send failed", "chunk": i}

    return {"status": "sent", "chunks": len(chunks)}




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
                supabase.table("users")
                .select("phone_number")
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
                            f"Linked phone number: {existing_user.data['phone_number']}\n\n"
                            "You'll receive news updates here automatically."
                        ),
                    },
                )
                return {"status": "already_linked"}
        except Exception as e:
            print(f"Error checking existing user: {e}")

        if len(parts) == 2:
            token_or_phone = parts[1].strip()

            # Try token-based linking first (automatic from website)
            if len(token_or_phone) == 64:  # hex token is 64 chars
                try:
                    # Verify token
                    token_result = (
                        supabase.table("telegram_tokens")
                        .select("*")
                        .eq("token", token_or_phone)
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

                    phone_number = token_data["phone_number"]

                    # Mark token as used
                    supabase.table("telegram_tokens").update({"used": True}).eq(
                        "token", token_or_phone
                    ).execute()

                    # Link chat_id to user directly in Supabase
                    try:
                        supabase.table("users").update({"chat_id": str(chat_id)}).eq("phone_number", phone_number).execute()
                        print(f"[save_chat_id] Successfully updated chat_id directly in Supabase for {phone_number}")
                    except Exception as e:
                        print(f"[save_chat_id] Direct Supabase update error: {e}")

                    # Also notify Next.js Vercel API
                    save_payload = {"phone_number": phone_number, "chat_id": chat_id}
                    try:
                        save_resp = requests.post(
                            "https://my-next-app-seven-delta.vercel.app/api/bots/save_chat_id",
                            json=save_payload,
                        )
                        print(f"[save_chat_id] Vercel API Status: {save_resp.status_code}, Response: {save_resp.text}")
                    except Exception as e:
                        print(f"[save_chat_id] Vercel API request error: {e}")

                    requests.post(
                        f"{TELEGRAM_API_URL}/sendMessage",
                        json={
                            "chat_id": chat_id,
                            "text": f"✅ Successfully linked to {phone_number}!\n\nYou'll now receive news updates here.",
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

            # Fallback: manual phone linking (old method)
            phone_number = token_or_phone
            try:
                check = (
                    supabase.table("users")
                    .select("*")
                    .eq("phone_number", phone_number)
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
                            f"❌ The phone number *{phone_number}* is not registered.\n\n"
                            "Please log in first:\n"
                            "https://my-next-app-seven-delta.vercel.app/"
                        ),
                        "parse_mode": "Markdown",
                    },
                )
                return {"status": "phone_not_found"}

            # Save chat_id directly in Supabase
            try:
                supabase.table("users").update({"chat_id": str(chat_id)}).eq("phone_number", phone_number).execute()
                print(f"[save_chat_id] Successfully updated chat_id directly in Supabase for {phone_number}")
            except Exception as e:
                print(f"[save_chat_id] Direct Supabase update error: {e}")

            # Also notify Next.js Vercel API
            save_payload = {"phone_number": phone_number, "chat_id": chat_id}
            try:
                save_resp = requests.post(
                    "https://my-next-app-seven-delta.vercel.app/api/bots/save_chat_id",
                    json=save_payload,
                )
                print(f"[save_chat_id] Vercel API Status: {save_resp.status_code}, Response: {save_resp.text}")
            except Exception as e:
                print(f"[save_chat_id] Vercel API request error: {e}")

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
                    "Or manually send: `/start your_phone_number`"
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