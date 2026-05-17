import json
from typing import List, Optional
from pydantic import BaseModel, Field
from openai import AsyncOpenAI
from ..config import OPENROUTER_API_KEY, GPT_MODEL, SITE_URL, SITE_NAME

# --- LLM Client ---
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
    complaints: List[str] = Field(default_factory=list)
    duration: Optional[str] = Field(None)
    medications_taken: List[str] = Field(default_factory=list)
    past_history: Optional[str] = Field(None)
    red_flags: List[str] = Field(default_factory=list)
    additional_info: Optional[str] = Field(None)
    summary: Optional[str] = Field(None)
    
    def to_html(self) -> str:
        blocks = []
        def format_value(v):
            if v is None: return ""
            if isinstance(v, list):
                items = [str(x) for x in v if x]
                return "; ".join(items)
            return str(v)

        def add_block(label, value, extra_class=""):
            val_str = format_value(value).strip('; ,')
            if val_str and val_str.lower() not in ["none", "null", "[]", "{}", "не указано", "нет данных"]:
                blocks.append(f'<div class="info-block {extra_class}"><div class="info-label">{label}</div><div class="info-value">{val_str}</div></div>')

        add_block("Резюме", self.summary)
        add_block("Жалобы и симптомы", self.complaints)
        add_block("Длительность", self.duration)
        add_block("Тревожные симптомы", self.red_flags, "warning")
        add_block("Лекарства и лечение", self.medications_taken)
        add_block("Анамнез пациентов", self.past_history)
        add_block("Дополнительная информация", self.additional_info)
        
        return f'<div class="summary-grid">{"".join(blocks)}</div>' if blocks else "Медицинские данные не обнаружены"

async def get_llm_summary(text: str, doctor_type: str = "default") -> str:
    if not llm: return "Суммаризация недоступна"
    if not text.strip() or len(text.strip()) < 5:
        return "Недостаточно данных для анализа"
        
    config = DOCTOR_PROMPTS.get(doctor_type, DOCTOR_PROMPTS["default"])
    schema = PatientSummary.model_json_schema()
    system_prompt = config["prompt"] + f"\n\nВАЖНО: Извлекай ТОЛЬКО ту информацию, которая буквально есть в тексте пациента. Ответь строго в формате JSON по схеме:\n{json.dumps(schema, ensure_ascii=False)}"

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
        if not content: return "Пустой ответ от ИИ"
        
        patient_data = PatientSummary.model_validate_json(content)
        return patient_data.to_html()
    except Exception as e:
        print(f"LLM Error: {e}")
        return "Ошибка при анализе текста нейросетью"
