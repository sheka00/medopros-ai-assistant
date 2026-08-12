import sys
import os
import uuid
import json
import shutil
import tempfile
import asyncio
import threading
import jwt

# Add parent directory to sys.path to import gender.py
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from gender import detect_gender
except ImportError:
    def detect_gender(**kwargs): return "androgynous"
from datetime import datetime, timedelta
from typing import List, Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from .config import (
    PORT, SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES,
    LOGIN_USER, LOGIN_PASSWORD, RECORDINGS_DIR, FAILED_DIR, SITE_URL, SITE_NAME,
    UGMK_API_KEY
)
from .database import history_manager, session_manager
from .services.llm import get_llm_summary, DOCTOR_PROMPTS, llm
from .services.asr import transcribe_audio_base64, filter_hallucinations
from .services.notifier import send_telegram_lead, send_telegram_alert

leads_lock = threading.RLock()

# --- Rate Limiter ---
limiter = Limiter(key_func=get_remote_address)

# --- Lifespan ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    if llm:
        print(f"🤖 LLM Initialized via OpenRouter.")
    
    async def periodic_cleanup():
        while True:
            try:
                history_manager.cleanup_old(days=30)
            except Exception as e:
                print(f"Cleanup error: {e}")
            await asyncio.sleep(86400)

    cleanup_task = asyncio.create_task(periodic_cleanup())
    yield
    cleanup_task.cancel()

# --- App Setup ---
app = FastAPI(lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

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

class CreateSessionRequest(BaseModel):
    doctor_type: str
    patient_name: Optional[str] = None

class UgmkQuestionsRequest(BaseModel):
    patient_name: str

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

# --- Routes ---

@app.post("/api/auth/login", response_model=Token)
async def login(user: UserLogin):
    if user.username != LOGIN_USER or user.password != LOGIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return {"access_token": create_access_token({"sub": user.username}), "token_type": "bearer"}

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

@app.post("/api/ugmk/questions")
async def ugmk_questions(req: UgmkQuestionsRequest, x_api_key: Optional[str] = Header(None)):
    if x_api_key != UGMK_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API Key")
        
    try:
        parts = req.patient_name.split()
        if len(parts) >= 3: gender = detect_gender(lastname=parts[0], firstname=parts[1], middlename=parts[2])
        elif len(parts) == 2: gender = detect_gender(lastname=parts[0], firstname=parts[1])
        else: gender = detect_gender(firstname=req.patient_name)
    except Exception:
        gender = "androgynous"

    q_base = [
        "1. Пожалуйста подробно опишите симптомы, которые Вас беспокоят. Если вы испытываете боль укажите ее локализацию, характер, интенсивность по шкале от 0 до 10 баллов. Как часто она возникает и как долго продолжается?",
        "2. Что может провоцировать, усиливать или облегчать Ваши симптомы?",
        "3. Как давно у Вас появилась данная проблема? Как развивалось Ваше заболевание? Какое лечение вы уже получали и какой был эффект?",
        "4. Какие еще болезни у Вас есть? Были ли тяжелые заболевания, травмы и операции?",
        "5. Бывали ли у Вас аллергические реакции на что-либо? Есть ли аллергия на лекарства?",
        "6. Испытываете ли постоянно или периодически Вы боль еще в каких-то областях или участках тела?"
    ]
    q_hads = "Оцените ваше текущее эмоциональное состояние: испытываете ли вы в последнее время тревогу, напряжение или сниженное настроение?"
    
    if gender == 'male':
        questions = q_base + ["7. Есть ли у Вас проблемы в сексуальной сфере? Испытываете ли Вы половое влечение? Устраивает ли Вас качество эрекции и продолжительность полового акта? Хотели бы Вы обсудить с врачом вопросы Вашей интимной жизни?", f"8. (Опросник HADS) {q_hads}"]
    elif gender == 'female':
        questions = q_base + ["7. Расскажите о своем менструальном цикле: сколько дней составляет Ваш цикл, сколько продолжаются кровянистые выделения, насколько они обильные, бывают ли сгустки крови или коричневые выделения. Насколько болезненны месячные?", "8. Актуален ли для Вас вопрос качества сексуальной жизни? Испытываете ли Вы боль или дискомфорт при половом акте? Можете ли достигнуть оргазма? Хотели бы Вы обсудить с врачом вопросы Вашей интимной жизни?", f"9. (Опросник HADS) {q_hads}"]
    else:
        questions = q_base + ["7. Актуален ли для Вас вопрос качества сексуальной жизни? Испытываете ли Вы боль или дискомфорт при половом акте? Хотели бы Вы обсудить с врачом вопросы Вашей интимной жизни?", f"8. (Опросник HADS) {q_hads}"]

    return {"gender": gender, "questions": questions}

async def convert_to_wav(src_path: str, dst_path: str):
    """Конвертирует аудио в 16kHz mono WAV. Кидает RuntimeError с реальной причиной от ffmpeg."""
    if not os.path.exists(src_path) or os.path.getsize(src_path) == 0:
        raise RuntimeError(f"Пустой или отсутствующий входной файл: {src_path}")

    process = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-y", "-i", src_path, "-ar", "16000", "-ac", "1", dst_path,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await process.communicate()
    err_tail = "\n".join(stderr.decode("utf-8", "replace").strip().splitlines()[-5:])

    if process.returncode != 0:
        raise RuntimeError(
            f"ffmpeg вернул код {process.returncode} "
            f"(вход: {os.path.getsize(src_path)} байт)\n{err_tail}"
        )
    if not os.path.exists(dst_path) or os.path.getsize(dst_path) == 0:
        raise RuntimeError(f"ffmpeg отработал, но {dst_path} пуст или не создан\n{err_tail}")

def preserve_failed_input(record_id: str, src_path: str, file: UploadFile) -> str:
    """Складывает не сконвертировавшийся файл в data/failed/ и возвращает описание для алерта."""
    size = os.path.getsize(src_path) if os.path.exists(src_path) else 0
    head = b""
    try:
        with open(src_path, "rb") as f: head = f.read(16)
    except OSError: pass

    info = (f"имя='{file.filename}' content_type='{file.content_type}' "
            f"размер={size} байт сигнатура={head.hex()}")

    try:
        os.makedirs(FAILED_DIR, exist_ok=True)
        # держим только последние 20 образцов, чтобы не забить диск
        old = sorted(os.listdir(FAILED_DIR))
        for name in old[:max(0, len(old) - 19)]:
            os.remove(os.path.join(FAILED_DIR, name))
        if size:
            shutil.copy(src_path, os.path.join(FAILED_DIR, f"{record_id}.bin"))
            info += f"\nОбразец сохранён: data/failed/{record_id}.bin"
    except OSError as e:
        info += f"\nНе удалось сохранить образец: {e}"

    return info

@app.post("/api/ugmk/transcribe")
async def ugmk_transcribe(file: UploadFile = File(...), x_api_key: Optional[str] = Header(None)):
    if x_api_key != UGMK_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API Key")
        
    record_id = str(uuid.uuid4())
    temp_path = os.path.join(tempfile.gettempdir(), f"{record_id}.wav")
    
    content = await file.read()
    def _save_file(path, data):
        with open(path, "wb") as f: f.write(data)
    await asyncio.to_thread(_save_file, temp_path, content)
    
    try:
        wav_path = os.path.join(tempfile.gettempdir(), f"{record_id}_conv.wav")
        await convert_to_wav(temp_path, wav_path)

        raw_text = await transcribe_audio_base64(wav_path)
        text = filter_hallucinations(raw_text)

        if not text or len(text.strip()) < 2:
            summary = {"error": "Речь не распознана"}
        else:
            summary = await get_llm_summary(text, "default", return_json=True)
            
        print(f"[{record_id}] Transcribed text: {text}")
        print(f"[{record_id}] Summary generated: {summary}")

        return {"id": record_id, "text": text, "summary": summary}
    except Exception as e:
        details = f"UGMK, обработка аудио ({record_id}):\n{e}"
        if isinstance(e, RuntimeError):  # сбой конвертации — сохраняем исходник для разбора
            details += "\n" + await asyncio.to_thread(preserve_failed_input, record_id, temp_path, file)
        print(f"UGMK Transcription error [{record_id}]: {details}")
        await send_telegram_alert(details)
        raise HTTPException(status_code=500, detail="Audio processing failed")
    finally:
        if os.path.exists(temp_path): os.remove(temp_path)
        if 'wav_path' in locals() and os.path.exists(wav_path): os.remove(wav_path)

@app.post("/transcribe", response_model=TranscriptionResponse)
@limiter.limit("10/minute")
async def transcribe_audio(
    request: Request,
    file: UploadFile = File(...), 
    authorization: Optional[str] = Header(None),
    token: Optional[str] = Header(None)
):
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
        raise HTTPException(status_code=401, detail="Authorization required")
    
    record_id = str(uuid.uuid4())
    temp_path = os.path.join(tempfile.gettempdir(), f"{record_id}.wav")
    
    content = await file.read()
    def _save_file(path, data):
        with open(path, "wb") as f: f.write(data)
    await asyncio.to_thread(_save_file, temp_path, content)
    
    try:
        wav_path = os.path.join(tempfile.gettempdir(), f"{record_id}_conv.wav")
        await convert_to_wav(temp_path, wav_path)

        raw_text = await transcribe_audio_base64(wav_path)
        text = filter_hallucinations(raw_text)

        if not text or len(text.strip()) < 2:
            summary = "Речь не распознана."
        else:
            summary = await get_llm_summary(text, doctor_type)
            
        print(f"[{record_id}] Transcribed text: {text}")
        print(f"[{record_id}] Summary generated: {summary}")

        permanent_path = os.path.join(RECORDINGS_DIR, f"{record_id}.wav")
        await asyncio.to_thread(shutil.copy, wav_path, permanent_path)
        
        def _add_history():
            history_manager.add({
                "id": record_id,
                "username": user_identity,
                "filename": f"{record_id}.wav",
                "text": text,
                "summary": summary,
                "doctor_type": doctor_type,
                "timestamp": datetime.now().strftime("%d.%m.%Y %H:%M:%S")
            })
        await asyncio.to_thread(_add_history)
        
        if os.path.exists(wav_path): os.remove(wav_path)
        return TranscriptionResponse(id=record_id, text=text, filename=f"{record_id}.wav", summary=summary)
    except Exception as e:
        details = f"Обработка аудио ({record_id}):\n{e}"
        if isinstance(e, RuntimeError):
            details += "\n" + await asyncio.to_thread(preserve_failed_input, record_id, temp_path, file)
        print(f"Transcription error [{record_id}]: {details}")
        await send_telegram_alert(details)
        raise HTTPException(status_code=500, detail="Audio processing failed")
    finally:
        if os.path.exists(temp_path): os.remove(temp_path)
        if 'wav_path' in locals() and os.path.exists(wav_path): os.remove(wav_path)

@app.get("/api/history", response_model=List[dict])
async def get_history(current_user: dict = Depends(get_current_user)):
    return history_manager.get_all()

@app.delete("/api/history/{record_id}")
async def delete_record(record_id: str, current_user: dict = Depends(get_current_user)):
    record = history_manager.get_by_id(record_id)
    if not record: raise HTTPException(status_code=404)
    
    path = os.path.join(RECORDINGS_DIR, record["filename"])
    if os.path.exists(path): os.remove(path)
    
    if history_manager.delete(record_id): return {"status": "success"}
    raise HTTPException(status_code=500)

@app.get("/audio/{filename}")
async def serve_audio(filename: str):
    safe_filename = os.path.basename(filename)
    if not safe_filename.endswith(".wav"):
        raise HTTPException(status_code=403, detail="Only .wav files are allowed")
        
    path = os.path.join(RECORDINGS_DIR, safe_filename)
    if not os.path.exists(path): raise HTTPException(status_code=404)
    return FileResponse(path, media_type="audio/wav")

@app.post("/api/contact")
@limiter.limit("3/minute")
async def contact_form(request: Request, req: ContactRequest):
    def _add_lead():
        leads_file = os.path.join(RECORDINGS_DIR, "leads.json")
        with leads_lock:
            leads = []
            if os.path.exists(leads_file):
                try:
                    with open(leads_file, "r") as f: leads = json.load(f)
                except: pass
            leads.append({**req.model_dump(), "timestamp": datetime.now().strftime("%d.%m.%Y %H:%M:%S")})
            with open(leads_file, "w") as f: json.dump(leads, f, indent=2, ensure_ascii=False)

    await asyncio.to_thread(_add_lead)

    await send_telegram_lead(req.name, req.clinic, req.phone)
    return {"status": "success", "message": "Заявка принята"}

# --- Static Files (SECURE MOUNTING) ---

# 1. Mount assets (relative paths for landing)
app.mount("/assets", StaticFiles(directory="static/landing/assets"), name="assets")

# 2. Mount Dashboard app
app.mount("/app", StaticFiles(directory="static/dashboard", html=True), name="app")

# 3. Serve Landing index.html on root
@app.get("/")
async def serve_landing():
    return FileResponse("static/landing/index.html")

# --- Run ---
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
