import os
import json
import uuid
from datetime import datetime, timedelta
from .config import RECORDINGS_DIR

HISTORY_FILE = os.path.join(RECORDINGS_DIR, "history.json")
SESSIONS_FILE = os.path.join(RECORDINGS_DIR, "sessions.json")

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
                item_date = datetime.strptime(item["timestamp"], "%d.%m.%Y %H:%M:%S")
                if item_date < threshold:
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

history_manager = HistoryManager(HISTORY_FILE)
session_manager = SessionManager(SESSIONS_FILE)
