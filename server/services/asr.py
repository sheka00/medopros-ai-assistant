import httpx
import base64
import asyncio
from ..config import OPENROUTER_API_KEY, AUDIO_MODEL, FALLBACK_AUDIO_MODELS, SITE_URL, SITE_NAME

async def transcribe_audio_base64(wav_path: str) -> str:
    if not OPENROUTER_API_KEY:
        return ""
    
    try:
        with open(wav_path, "rb") as f:
            base64_audio = base64.b64encode(f.read()).decode("utf-8")

        models_to_try = [AUDIO_MODEL] + [m.strip() for m in FALLBACK_AUDIO_MODELS if m.strip()]
        
        unique_models = []
        for m in models_to_try:
            if m and m not in unique_models:
                unique_models.append(m)

        max_retries_per_model = 5
        
        last_error = "Неизвестная ошибка"
        async with httpx.AsyncClient(timeout=120.0) as client:
            for model_name in unique_models:
                for attempt in range(max_retries_per_model):
                    try:
                        response = await client.post(
                            url="https://openrouter.ai/api/v1/audio/transcriptions",
                            headers={
                                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                                "Content-Type": "application/json",
                                "HTTP-Referer": SITE_URL,
                                "X-Title": SITE_NAME,
                            },
                            json={
                                "model": model_name,
                                "input_audio": {
                                    "data": base64_audio,
                                    "format": "wav"
                                },
                                "prompt": "Это медицинская запись. Пациент описывает жалобы на здоровье."
                            }
                        )
                        
                        if response.status_code == 200:
                            result = response.json()
                            return result.get("text", "").strip()
                        # Добавили 403, 400 в список повторов на случай если VPN временно отвалился и отдает RU IP
                        elif response.status_code in [403, 400, 429, 500, 502, 503, 504]:
                            last_error = f"HTTP {response.status_code}: {response.text}"
                            if attempt < max_retries_per_model - 1:
                                await asyncio.sleep(2 ** attempt)
                            continue
                        else:
                            last_error = f"HTTP {response.status_code}: {response.text}"
                            print(f"ASR Fatal Error ({model_name}): {last_error}")
                            break # Критическая ошибка, пробуем следующую модель
                    except httpx.RequestError as e:
                        last_error = f"Network Error: {e}"
                        print(f"ASR Network Error ({model_name}): {e}")
                        if attempt < max_retries_per_model - 1:
                            await asyncio.sleep(2 ** attempt)
                        else:
                            break # Сеть не работает для этой модели, переходим к следующей

        from .notifier import send_telegram_alert
        await send_telegram_alert(f"Все {len(unique_models)} ASR моделей отказали.\nПоследняя ошибка ({unique_models[-1]}):\n{last_error}")
        return ""
    except Exception as e:
        print(f"ASR Exception: {e}")
        try:
            from .notifier import send_telegram_alert
            await send_telegram_alert(f"Критическая ошибка ASR (Exception):\n{str(e)}")
        except: pass
        return ""

def filter_hallucinations(text: str) -> str:
    HALLUCINATIONS = [
        "thank you.", "thank you", "thanks for watching.", 
        "подпишитесь на канал", "продолжение следует", 
        "bye.", "bye bye", "you", "..."
    ]
    cleaned_text = text.strip().lower().rstrip(".")
    if cleaned_text in [h.lower().rstrip(".") for h in HALLUCINATIONS]:
        return ""
    return text
