import httpx
import base64
import asyncio
from ..config import OPENROUTER_API_KEY, AUDIO_MODEL, SITE_URL, SITE_NAME

async def transcribe_audio_base64(wav_path: str) -> str:
    if not OPENROUTER_API_KEY:
        return ""
    
    try:
        with open(wav_path, "rb") as f:
            base64_audio = base64.b64encode(f.read()).decode("utf-8")

        max_retries = 3
        async with httpx.AsyncClient(timeout=120.0) as client:
            for attempt in range(max_retries):
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
                            "model": AUDIO_MODEL,
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
                    elif response.status_code in [429, 500, 502, 503, 504]:
                        if attempt < max_retries - 1:
                            await asyncio.sleep(2 ** attempt)
                        continue
                    else:
                        print(f"ASR Fatal Error: {response.status_code} - {response.text}")
                        break
                except httpx.RequestError as e:
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2 ** attempt)
        return ""
    except Exception as e:
        print(f"ASR Exception: {e}")
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
