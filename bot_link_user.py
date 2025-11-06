import os
import random
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv
from supabase import create_client
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from telegram.error import TelegramError

# Gmail API
import base64
from email.mime.text import MIMEText
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# -------------------------------
# Load environment variables
# -------------------------------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
GOOGLE_TOKEN_JSON = os.getenv("GOOGLE_TOKEN_JSON")  # path to token.json

# -------------------------------
# Connect to Supabase
# -------------------------------
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

# -------------------------------
# OTP storage
# -------------------------------
otp_store = {}  # {email: (otp, expiration_timestamp)}

# -------------------------------
# Send OTP via Gmail API
# -------------------------------
def send_otp_email_via_gmail(to_email, otp):
    creds = Credentials.from_authorized_user_file(
        GOOGLE_TOKEN_JSON,
        ["https://www.googleapis.com/auth/gmail.send"]
    )
    service = build("gmail", "v1", credentials=creds)
    
    message = MIMEText(f"Your OTP for Telegram bot linking is: {otp}")
    message["to"] = to_email
    message["subject"] = "Your Telegram Bot OTP"

    raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
    body = {"raw": raw_message}
    service.users().messages().send(userId="me", body=body).execute()

# -------------------------------
# /start command handler
# -------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    chat_id = update.message.chat_id
    print(f"👤 {user.first_name} started the bot (id={chat_id})")

    try:
        existing = supabase.table("user").select("*").eq("chat_id", chat_id).execute()
        if existing.data and existing.data[0].get("phone_number") and existing.data[0].get("username"):
            await update.message.reply_text(
                "✅ You are already linked with the bot! No need to start again."
            )
            return

        await update.message.reply_text(
            "👋 Welcome! Please enter your email to link your Telegram account:"
        )
        context.user_data["awaiting_email"] = True

    except TelegramError as e:
        print("❌ Telegram error:", e)
        await update.message.reply_text(
            "⚠️ Unable to contact Telegram servers. Please try again later."
        )

# -------------------------------
# Handle email input
# -------------------------------
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id

    if context.user_data.get("awaiting_email"):
        email = update.message.text.strip()
        result = supabase.table("user").select("*").eq("email", email).execute()

        if result.data:
            existing_phone = result.data[0].get("phone_number")
            if existing_phone:
                # Email is linked with another phone number
                context.user_data["email"] = email
                context.user_data["awaiting_email"] = False
                await update.message.reply_text(
                    "⚠️ This email is already connected with the bot using another phone number.\n"
                    "Do you want to change the linked phone number? (yes/no)"
                )
                context.user_data["awaiting_change_confirmation"] = True
            else:
                # No phone linked, ask to share contact
                context.user_data["email"] = email
                context.user_data["awaiting_email"] = False
                contact_button = KeyboardButton("📱 Share my contact", request_contact=True)
                markup = ReplyKeyboardMarkup([[contact_button]], one_time_keyboard=True, resize_keyboard=True)
                await update.message.reply_text(
                    "✅ Email verified! Now please share your phone number:", reply_markup=markup
                )
        else:
            context.user_data["awaiting_email"] = False
            await update.message.reply_text(
                "❌ Email not found. Please login via the web first:\n"
                "🌐 my-next-hgkfl4ycg-livindas-projects.vercel.app"
            )

    elif context.user_data.get("awaiting_change_confirmation"):
        reply = update.message.text.strip().lower()
        email = context.user_data.get("email")
        if reply == "yes":
            # Generate OTP
            otp = str(random.randint(1000, 999999))
            expiration = datetime.now() + timedelta(minutes=5)
            otp_store[email] = (otp, expiration)

            # Send OTP via Gmail API
            send_otp_email_via_gmail(email, otp)
            await update.message.reply_text(
                "✅ OTP sent to your email. Please enter the OTP to confirm phone number change:"
            )
            context.user_data["awaiting_otp"] = True
            context.user_data["awaiting_change_confirmation"] = False
        else:
            await update.message.reply_text("Operation canceled. You cannot change the phone number.")
            context.user_data.clear()

    elif context.user_data.get("awaiting_otp"):
        otp_input = update.message.text.strip()
        email = context.user_data.get("email")
        otp, expiration = otp_store.get(email, (None, None))

        if otp and datetime.now() <= expiration and otp_input == otp:
            # OTP correct, remove old phone number and chat_id
            supabase.table("user").update({"phone_number": None, "chat_id": None}).eq("email", email).execute()
            await update.message.reply_text("✅ OTP verified! Please share your new contact now.")
            contact_button = KeyboardButton("📱 Share my contact", request_contact=True)
            markup = ReplyKeyboardMarkup([[contact_button]], one_time_keyboard=True, resize_keyboard=True)
            await update.message.reply_text("📲 Share your phone number:", reply_markup=markup)
            context.user_data["awaiting_otp"] = False
            otp_store.pop(email, None)
        else:
            await update.message.reply_text(
                "❌ OTP is incorrect or expired. Do you want to try again? (yes/no)"
            )
            context.user_data["awaiting_otp_retry"] = True
            context.user_data["awaiting_otp"] = False

    elif context.user_data.get("awaiting_otp_retry"):
        reply = update.message.text.strip().lower()
        email = context.user_data.get("email")
        if reply == "yes":
            # Resend OTP
            otp = str(random.randint(1000, 999999))
            expiration = datetime.now() + timedelta(minutes=5)
            otp_store[email] = (otp, expiration)
            send_otp_email_via_gmail(email, otp)
            await update.message.reply_text("✅ OTP resent. Please enter the OTP:")
            context.user_data["awaiting_otp"] = True
        else:
            await update.message.reply_text("Operation canceled.")
            context.user_data.clear()

# -------------------------------
# Handle contact sharing
# -------------------------------
async def contact_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.contact
    phone_number = contact.phone_number
    chat_id = update.message.chat_id
    email = context.user_data.get("email")

    if not email:
        await update.message.reply_text(
            "⚠️ Please start with /start and enter your email first."
        )
        return

    try:
        response = supabase.table("user").update(
            {"chat_id": chat_id, "phone_number": phone_number}
        ).eq("email", email).execute()

        print("📝 Updated user:", response)
        await update.message.reply_text("✅ Your Telegram has been linked successfully!")
        context.user_data.clear()

    except Exception as e:
        print("❌ Error saving to Supabase:", e)
        await update.message.reply_text(
            "⚠️ Failed to link your Telegram account. Please try again later."
        )

# -------------------------------
# Run the bot
# -------------------------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_handler(MessageHandler(filters.CONTACT, contact_handler))
    print("🤖 Bot is running... Press Ctrl+C to stop.")
    app.run_polling()

if __name__ == "__main__":
    main()
