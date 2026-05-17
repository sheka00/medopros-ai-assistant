import httpx
from datetime import datetime
from ..config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

async def send_telegram_lead(name: str, clinic: str, phone: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram notify skipped: configurations missing")
        return False
    
    message = (
        f"🚀 *ГОРЯЧАЯ ЗАЯВКА - МЕДОПРОС*\n\n"
        f"👤 *Имя:* {name}\n"
        f"🏥 *Клиника:* {clinic}\n"
        f"📞 *Телефон:* {phone}\n"
        f"🕒 *Время:* {datetime.now().strftime('%H:%M:%S')}"
    )
    
    try:
        async with httpx.AsyncClient() as client:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "Markdown"
            }
            res = await client.post(url, json=payload, timeout=10.0)
            return res.is_success
    except Exception as e:
        print(f"❌ Telegram notify error: {e}")
        return False
