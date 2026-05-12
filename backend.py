import os
import asyncio
import uuid
import json
import base64
import shutil
import tempfile
import jwt
import subprocess
import httpx
from datetime import datetime, timedelta
from typing import List, Optional, Any
from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from contextlib import asynccontextmanager
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

# --- Config ---
RECORDINGS_DIR = "recordings"
HISTORY_FILE = os.path.join(RECORDINGS_DIR, "history.json")
SECRET_KEY = os.getenv("SECRET_KEY", "medopros_secret_key_change_me")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
GPT_MODEL = os.getenv("GPT_MODEL", "openrouter/free")
ALGORITHM = "HS256"
AUDIO_MODEL = os.getenv("AUDIO_MODEL", "openai/whisper-large-v3-turbo")
SITE_URL = os.getenv("SITE_URL", "http://localhost:8001")
SITE_NAME = os.getenv("SITE_NAME", "Medopros MVP")
PORT = int(os.getenv("PORT", 8001))
LOGIN_USER = os.getenv("LOGIN_USER", "admin")
LOGIN_PASSWORD = os.getenv("LOGIN_PASSWORD", "admin")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7 # 1 week

if not os.path.exists(RECORDINGS_DIR):
    os.makedirs(RECORDINGS_DIR)

# --- Global State ---
llm = None

if OPENROUTER_API_KEY:
    llm = AsyncOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_API_KEY,
        timeout=60.0,
        default_headers={
            "HTTP-Referer": SITE_URL,
            "X-Title": SITE_NAME
        }
    )

class HistoryManager:
    def __init__(self, filepath):
        self.filepath = filepath
        if not os.path.exists(self.filepath):
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump([], f)
    
    def get_all(self):
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    
    def add(self, entry):
        history = self.get_all()
        history.insert(0, entry)
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    
    def get_by_id(self, record_id):
        history = self.get_all()
        for item in history:
            if item["id"] == record_id:
                return item
        return None

    def delete(self, record_id):
        history = self.get_all()
        new_history = [item for item in history if item["id"] != record_id]
        if len(new_history) < len(history):
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(new_history, f, ensure_ascii=False, indent=2)
            return True
        return False

    def cleanup_old(self, days=30):
        history = self.get_all()
        now = datetime.now()
        threshold = now - timedelta(days=days)
        
        new_history = []
        deleted_count = 0
        
        for item in history:
            try:
                # Парсим дату формата "06.05.2026 12:32:44"
                item_date = datetime.strptime(item["timestamp"], "%d.%m.%Y %H:%M:%S")
                if item_date < threshold:
                    # Удаляем файл
                    path = os.path.join(RECORDINGS_DIR, item["filename"])
                    if os.path.exists(path):
                        os.remove(path)
                    deleted_count += 1
                    continue
            except Exception as e:
                print(f"Error parsing date or deleting file: {e}")
            
            new_history.append(item)
            
        if deleted_count > 0:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(new_history, f, ensure_ascii=False, indent=2)
            print(f"🧹 Auto-cleanup: deleted {deleted_count} old records.")

history_manager = HistoryManager(HISTORY_FILE)

@asynccontextmanager
async def lifespan(app: FastAPI):
    if llm:
        print(f"🤖 LLM (OpenRouter via SDK) initialized. Model: {GPT_MODEL}")
    else:
        print("⚠️ OPENROUTER_API_KEY missing.")
    
    # Фоновая задача на очистку
    async def periodic_cleanup():
        while True:
            try:
                history_manager.cleanup_old(days=30)
            except Exception as e:
                print(f"Cleanup task error: {e}")
            await asyncio.sleep(86400) # Раз в сутки

    cleanup_task = asyncio.create_task(periodic_cleanup())
    
    yield
    cleanup_task.cancel()

class SessionManager:
    def __init__(self, filepath):
        self.filepath = filepath
        if not os.path.exists(self.filepath):
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump({}, f)
    
    def get_all(self):
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    
    def create(self, doctor_type, patient_name=None):
        sessions = self.get_all()
        token = str(uuid.uuid4())
        sessions[token] = {
            "token": token,
            "doctor_type": doctor_type,
            "patient_name": patient_name,
            "is_used": False,
            "created_at": datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        }
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(sessions, f, ensure_ascii=False, indent=2)
        return sessions[token]
    
    def get_by_token(self, token):
        sessions = self.get_all()
        return sessions.get(token)

    def mark_used(self, token):
        sessions = self.get_all()
        if token in sessions:
            sessions[token]["is_used"] = True
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(sessions, f, ensure_ascii=False, indent=2)

session_manager = SessionManager(os.path.join(RECORDINGS_DIR, "sessions.json"))

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Schemas ---
class UserLogin(BaseModel):
    username: str
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str

class TranscriptionResponse(BaseModel):
    id: str
    text: str
    filename: str
    summary: Optional[str] = None

class ContactRequest(BaseModel):
    name: str
    clinic: str
    phone: str

# --- Prompts ---
DOCTOR_PROMPTS = {
    "therapist": {
        "title": "Терапевт",
        "questions": ["На что жалуетесь?", "Как давно это началось?", "Есть ли температура?", "Какие лекарства принимали?"],
        "prompt": "Ты — профессиональный терапевт. Извлеки жалобы, длительность, наличие температуры и принятые меры."
    },
    "cardiologist": {
        "title": "Кардиолог",
        "questions": ["Беспокоит ли давление?", "Есть ли одышка?", "Болит ли в груди?", "Как переносите нагрузки?"],
        "prompt": "Ты — профессиональный кардиолог. Извлеки данные о давлении, болях в груди, одышке и физической активности."
    },
    "neurologist": {
        "title": "Невролог",
        "questions": ["Беспокоят ли головные боли?", "Есть ли головокружение?", "Онемение в конечностях?", "Нарушение сна?"],
        "prompt": "Ты — профессиональный невролог. Извлеки данные о характере болей, головокружении и неврологических симптомах."
    },
    "default": {
        "title": "Общий осмотр",
        "questions": ["Что вас беспокоит?", "Когда появились симптомы?", "Что уже пробовали лечить?", "Аллергии?"],
        "prompt": "Ты — медицинский ассистент. Извлеки жалобы, анамнез и текущее состояние."
    }
}

class PatientSummary(BaseModel):
    complaints: List[str] = Field(default_factory=list, description="Список жалоб, строго извлеченных из текста пациента. Не придумывать.")
    duration: Optional[str] = Field(None, description="Длительность симптомов (указывать ТОЛЬКО если пациент об этом сказал).")
    medications_taken: List[str] = Field(default_factory=list, description="Препараты, которые принимал пациент (ТОЛЬКО из текста).")
    past_history: Optional[str] = Field(None, description="Анамнез, болезни (ТОЛЬКО если упомянуты в записи).")
    red_flags: List[str] = Field(default_factory=list, description="Тревожные симптомы, которые пациент РЕАЛЬНО назвал. Не писать общие медицинские угрозы от себя!")
    additional_info: Optional[str] = Field(None, description="Любая другая потенциально важная информация, которую сказал пациент, но которая не подходит в другие категории.")
    summary: Optional[str] = Field(None, description="Краткое резюме сказанного пациентом.")
    
    def to_html(self) -> str:
        blocks = []
        
        def format_value(v):
            if v is None: return ""
            if isinstance(v, list):
                items = []
                for item in v:
                    if isinstance(item, dict):
                        items.append(", ".join(str(x) for x in item.values() if x is not None and str(x).strip()))
                    else:
                        items.append(str(item))
                return "; ".join(x for x in items if x)
            if isinstance(v, dict):
                return ", ".join(str(x) for x in v.values() if x is not None and str(x).strip())
            return str(v)

        def add_block(label, value, extra_class=""):
            val_str = format_value(value).strip('; ,')
            if val_str and val_str.lower() not in ["none", "null", "[]", "{}", "не указано", "нет данных", "отсутствует", "не указана", "неизвестно", "не применимо", "n/a", "отрицает"]:
                blocks.append(f'<div class="info-block {extra_class}"><div class="info-label">{label}</div><div class="info-value">{val_str}</div></div>')

        add_block("Резюме", self.summary)
        add_block("Жалобы и симптомы", self.complaints)
        add_block("Длительность", self.duration)
        add_block("Тревожные симптомы", self.red_flags, "warning")
        add_block("Лекарства и лечение", self.medications_taken)
        add_block("Анамнез пациентов", self.past_history)
        add_block("Дополнительная информация", self.additional_info)
        
        if not blocks:
            return "Медицинские данные не обнаружены"
            
        return f'<div class="summary-grid">{"".join(blocks)}</div>'

# --- Auth Helpers ---
def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(authorization: Optional[str] = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization")
    try:
        token = authorization.split(" ")[1]
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return {"username": payload.get("sub")}
    except Exception:
        raise HTTPException(status_code=401, detail="Session expired")

# --- LLM Logic ---

async def get_llm_summary(text: str, doctor_type: str = "default") -> str:
    if not llm: return "Суммаризация недоступна"
    if not text.strip() or len(text.strip()) < 5:
        return "Недостаточно данных для анализа"
        
    config = DOCTOR_PROMPTS.get(doctor_type, DOCTOR_PROMPTS["default"])
    
    # Автоматически генерируем схему ожидаемого JSON
    schema = PatientSummary.model_json_schema()
    system_prompt = config["prompt"] + f"\n\nВАЖНО: Извлекай ТОЛЬКО ту информацию, которая буквально есть в тексте пациента. Если пациент не назвал симптом или длительность, оставляй поле пустым (null) или пустым списком []. КАТЕГОРИЧЕСКИ ЗАПРЕЩАЕТСЯ придумывать диагнозы или писать общие медицинские справки.\nОбязательно ответь строго в формате JSON, соответствующем этой схеме:\n{json.dumps(schema, ensure_ascii=False)}"

    try:
        response = await llm.chat.completions.create(
            model=GPT_MODEL,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text}
            ]
        )
        content = response.choices[0].message.content
        if not content: return "Нейросеть вернула пустой ответ"
        
        print(f"LLM Raw Content: {content[:100]}...")
        
        # Чистый и безопасный парсинг прямо в объект
        patient_data = PatientSummary.model_validate_json(content)
        return patient_data.to_html()
        
    except Exception as e:
        print(f"Summarization error or JSON mismatch: {e}")
        # Запасной вариант (fallback), если модель вернула не JSON или нарушила схему
        content = locals().get('content', '')
        if content:
            return f'<div class="info-block"><div class="info-label">Ответ ИИ</div><div class="info-value" style="white-space: pre-wrap;">{content}</div></div>'
        return "Ошибка при анализе текста нейросетью"

# --- Endpoints ---
@app.post("/api/auth/login", response_model=Token)
async def login(user: UserLogin):
    if user.username != LOGIN_USER or user.password != LOGIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return {"access_token": create_access_token({"sub": user.username}), "token_type": "bearer"}

# --- Session Endpoints ---
class CreateSessionRequest(BaseModel):
    doctor_type: str
    patient_name: Optional[str] = None

@app.post("/api/sessions/create")
async def create_session(req: CreateSessionRequest):
    return session_manager.create(req.doctor_type, req.patient_name)

@app.get("/api/sessions/{token}")
async def get_session(token: str):
    session = session_manager.get_by_token(token)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    if session.get("is_used"):
        raise HTTPException(status_code=403, detail="Session link already used")
    
    doc_config = DOCTOR_PROMPTS.get(session["doctor_type"], DOCTOR_PROMPTS["default"])
    return {**session, "config": doc_config}

@app.post("/transcribe", response_model=TranscriptionResponse)
async def transcribe_audio(
    file: UploadFile = File(...), 
    authorization: Optional[str] = Header(None),
    token: Optional[str] = Header(None)
):
    # Determine user or session
    user_identity = None
    doctor_type = "default"
    
    if authorization:
        user = await get_current_user(authorization)
        user_identity = user["username"]
    elif token:
        session = session_manager.get_by_token(token)
        if not session or session["is_used"]:
             raise HTTPException(status_code=403, detail="Invalid or used token")
        user_identity = f"Пациент: {session.get('patient_name', 'Неизвестно')}"
        doctor_type = session["doctor_type"]
        session_manager.mark_used(token)
    else:
        user_identity = "Анонимный гость"
        doctor_type = "default"
    
    record_id = str(uuid.uuid4())
    temp_path = os.path.join(tempfile.gettempdir(), f"{record_id}.wav")
    
    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    try:
        # Convert using ffmpeg to a guaranteed WAV format (16kHz, mono)
        wav_path = os.path.join(tempfile.gettempdir(), f"{record_id}_converted.wav")
        # -y (overwrite), -i (input), -ar 16000 (sample rate), -ac 1 (mono)
        cmd = ["ffmpeg", "-y", "-i", temp_path, "-ar", "16000", "-ac", "1", wav_path]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

        print(f"ASR starting for {record_id} via OpenRouter API...")
        
        with open(wav_path, "rb") as f:
            base64_audio = base64.b64encode(f.read()).decode("utf-8")

        text = ""
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
                            "prompt": "Это медицинская запись. Пациент описывает жалобы на здоровье, симптомы и самочувствие."
                        }
                    )
                    
                    if response.status_code == 200:
                        result = response.json()
                        text = result.get("text", "").strip()
                        break
                    elif response.status_code in [429, 500, 502, 503, 504]:
                        print(f"OpenRouter API error (Attempt {attempt+1}/{max_retries}): {response.status_code} - {response.text}")
                        if attempt < max_retries - 1:
                            await asyncio.sleep(2 ** attempt)
                        continue
                    else:
                        print(f"OpenRouter API fatal error: {response.status_code} - {response.text}")
                        break
                except httpx.RequestError as e:
                    print(f"Network error (Attempt {attempt+1}/{max_retries}): {e}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2 ** attempt)

        print(f"ASR Finished. Total Text Length: {len(text)}")

        # --- Filter Hallucinations ---
        HALLUCINATIONS = [
            "thank you.", "thank you", "thanks for watching.", 
            "подпишитесь на канал", "продолжение следует", 
            "bye.", "bye bye", "you", "..."
        ]
        
        cleaned_text = text.strip().lower().rstrip(".")
        if cleaned_text in [h.lower().rstrip(".") for h in HALLUCINATIONS]:
            print(f"⚠️ Filtered Whisper hallucination: '{text}'")
            text = ""

        if not text or len(text.strip()) < 2:
            summary = "Речь не распознана. Пожалуйста, говорите громче или ближе к микрофону."
        else:
            summary = await get_llm_summary(text, doctor_type)

        # Save Permanent
        permanent_path = os.path.join(RECORDINGS_DIR, f"{record_id}.wav")
        shutil.copy(wav_path, permanent_path)
        
        history_manager.add({
            "id": record_id,
            "username": user_identity,
            "filename": f"{record_id}.wav",
            "text": text,
            "summary": summary,
            "doctor_type": doctor_type,
            "timestamp": datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        })
        
        # Cleanup converted wav
        if os.path.exists(wav_path): os.remove(wav_path)

        return TranscriptionResponse(id=record_id, text=text, filename=f"{record_id}.wav", summary=summary)
    except Exception as e:
        print(f"Transcription error: {e}")
        raise HTTPException(status_code=500, detail="Audio processing failed")
    finally:
        if os.path.exists(temp_path): os.remove(temp_path)

@app.get("/api/history", response_model=List[dict])
async def get_history():
    return history_manager.get_all()

@app.delete("/api/history/{record_id}")
async def delete_record(record_id: str):
    record = history_manager.get_by_id(record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")
    
    # Remove file
    path = os.path.join(RECORDINGS_DIR, record["filename"])
    if os.path.exists(path):
        os.remove(path)
    
    # Remove from history
    if history_manager.delete(record_id):
        return {"status": "success"}
    else:
        raise HTTPException(status_code=500, detail="Delete failed")

@app.get("/audio/{filename}")
async def serve_audio(filename: str):
    path = os.path.join(RECORDINGS_DIR, filename)
    if not os.path.exists(path): raise HTTPException(status_code=404)
    from fastapi.responses import FileResponse
    return FileResponse(path, media_type="audio/wav")

@app.post("/api/contact")
async def contact_form(req: ContactRequest):
    # Log the request
    print(f"📩 New Lead: {req.name} from {req.clinic} ({req.phone})")
    
    # Save to history file for redundancy
    leads_file = os.path.join(RECORDINGS_DIR, "leads.json")
    leads = []
    if os.path.exists(leads_file):
        try:
            with open(leads_file, "r") as f: leads = json.load(f)
        except: pass
    
    leads.append({**req.model_dump(), "timestamp": datetime.now().strftime("%d.%m.%Y %H:%M:%S")})
    with open(leads_file, "w") as f: json.dump(leads, f, indent=2, ensure_ascii=False)

    # Send Notification to Telegram
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        message = (
            f"🚀 *ГОРЯЧАЯ ЗАЯВКА - МЕДОПРОС*\n\n"
            f"👤 *Имя:* {req.name}\n"
            f"🏥 *Клиника:* {req.clinic}\n"
            f"📞 *Телефон:* {req.phone}\n"
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
                if not res.is_success:
                    print(f"❌ Telegram notify failed: {res.text}")
        except Exception as e:
            print(f"❌ Telegram notify error: {e}")
    else:
        print("⚠️ Telegram notify skipped: configurations missing in .env")

    return {"status": "success", "message": "Заявка принята"}

app.mount("/assets", StaticFiles(directory="assets"), name="assets")
app.mount("/app", StaticFiles(directory="app", html=True), name="app")
app.mount("/", StaticFiles(directory="./", html=True), name="landing")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
