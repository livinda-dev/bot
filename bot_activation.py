import requests

BOT_TOKEN = "8978490291:AAHSTv1HveKi7sAxyFaVfotDnrX0Qo5vXJk"
WEBHOOK_URL = "https://news-pro-5cab.onrender.com/webhook"  # Replace <your-render-url>
response = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={WEBHOOK_URL}")
print(response.json())
