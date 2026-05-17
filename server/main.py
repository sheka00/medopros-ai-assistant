import os
import uuid
import json
import shutil
import tempfile
import asyncio
import subprocess
import jwt
from datetime import datetime, timedelta
from typing import List, Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .config import (
    PORT, SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES,
    LOGIN_USER, LOGIN_PASSWORD, RECORDINGS_DIR, SITE_URL, SITE_NAME
)
from .database import history_manager, session_manager
from .services.llm import get_llm_summary, DOCTOR_PROMPTS, llm
from .services.asr import transcribe_audio_base64, filter_hallucinations
from .services.notifier import send_telegram_lead

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

@app.post("/transcribe", response_model=TranscriptionResponse)
async def transcribe_audio(
    file: UploadFile = File(...), 
    authorization: Optional[str] = Header(None),
    token: Optional[str] = Header(None)
):
    user_identity = "Анонимный гость"
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
    
    record_id = str(uuid.uuid4())
    temp_path = os.path.join(tempfile.gettempdir(), f"{record_id}.wav")
    
    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    try:
        wav_path = os.path.join(tempfile.gettempdir(), f"{record_id}_conv.wav")
        cmd = ["ffmpeg", "-y", "-i", temp_path, "-ar", "16000", "-ac", "1", wav_path]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

        raw_text = await transcribe_audio_base64(wav_path)
        text = filter_hallucinations(raw_text)

        if not text or len(text.strip()) < 2:
            summary = "Речь не распознана."
        else:
            summary = await get_llm_summary(text, doctor_type)

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
async def contact_form(req: ContactRequest):
    leads_file = os.path.join(RECORDINGS_DIR, "leads.json")
    leads = []
    if os.path.exists(leads_file):
        try:
            with open(leads_file, "r") as f: leads = json.load(f)
        except: pass
    
    leads.append({**req.model_dump(), "timestamp": datetime.now().strftime("%d.%m.%Y %H:%M:%S")})
    with open(leads_file, "w") as f: json.dump(leads, f, indent=2, ensure_ascii=False)

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
