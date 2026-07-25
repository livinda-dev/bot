import requests

BOT_TOKEN = "8465591582:AAGaAWxKpsu0mX8rM-pHMzvDPPeWrIG4Rgg"
WEBHOOK_URL = "https://telegram-bot-w1as.onrender.com/webhook"  # Replace <your-render-url>
requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={WEBHOOK_URL}")
