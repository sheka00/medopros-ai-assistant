"""
gender.py — определение пола по русскому имени / отчеству / фамилии.

Зависимость: pip install pytrovich

Использование:
    from gender import detect_gender

    detect_gender(firstname="Женя")                          -> "androgynous"
    detect_gender(firstname="Женя", middlename="Иванович")   -> "male"
    detect_gender(firstname="Женя", middlename="Ивановна")   -> "female"
    detect_gender(firstname="Любовь")                        -> "female"
    detect_gender(firstname="Мария", lastname="Иванов")      -> "female"

Возвращаемые значения:
    "male"        — мужской пол
    "female"      — женский пол
    "androgynous" — не удалось определить (Женя без отчества,
                    инициалы, неизменяемые фамилии и т.п.)
"""

from pytrovich.detector import PetrovichGenderDetector
from pytrovich.enums import Gender

_detector = PetrovichGenderDetector()

# Имена, которые pytrovich не распознаёт:
#   - слова-омонимы (Вера, Надежда, Любовь)
#   - Ё-варианты без Е-формы в словаре (Артём, Фёдор)
#   - редкие / разговорные формы
# Ключи — нижний регистр
_EXCEPTIONS: dict[str, str] = {
    # Слова-омонимы / нарицательные
    "любовь":    "female",
    "надежда":   "female",
    "вера":      "female",
    # Ё-варианты
    "артём":     "male",
    "фёдор":     "male",
    "тимофей":   "male",
    "матвей":    "male",
    "алёна":     "female",
    # Старорусские
    "рюрик":     "male",
    "святослав": "male",
    "изяслав":   "male",
    "мирослава": "female",
    "ярослава":  "female",
    # Разговорные сокращения
    "лёша":      "male",
    "лёня":      "male",
    "сёма":      "male",
}


def detect_gender(
    firstname:  str | None = None,
    middlename: str | None = None,
    lastname:   str | None = None,
) -> str:
    """
    Определяет пол по имени / отчеству / фамилии.

    Приоритет: отчество → словарь исключений → pytrovich(имя) → фамилия.
    При конфликте имя vs фамилия — побеждает имя.

    Параметры
    ----------
    firstname  : имя          (например "Женя")
    middlename : отчество     (например "Иванович")
    lastname   : фамилия      (например "Иванов")

    Возвращает
    ----------
    "male" | "female" | "androgynous"
    """
    firstname  = (firstname  or "").strip() or None
    middlename = (middlename or "").strip() or None
    lastname   = (lastname   or "").strip() or None

    # Нечего анализировать
    if not any([firstname, middlename, lastname]):
        return "androgynous"

    # ── 1. Отчество — наивысший приоритет (~100% точность) ──────────────
    if middlename:
        return _from_pytrovich(middlename=middlename)

    # ── 2. Словарь исключений для имени ─────────────────────────────────
    if firstname:
        exc = _EXCEPTIONS.get(firstname.lower())
        if exc:
            return exc

    # ── 3. pytrovich по имени и фамилии, с разрешением конфликта ────────
    g_first = _from_pytrovich(firstname=firstname) if firstname else None
    g_last  = _from_pytrovich(lastname=lastname)   if lastname  else None

    if g_first and g_last:
        if g_first == g_last:
            return g_first                   # полное согласие
        if g_first != "androgynous":
            return g_first                   # имя надёжнее фамилии
        return g_last                        # имя неопределённо — берём фамилию

    return g_first or g_last or "androgynous"


def _from_pytrovich(
    firstname:  str | None = None,
    middlename: str | None = None,
    lastname:   str | None = None,
) -> str:
    """Тонкая обёртка над PetrovichGenderDetector."""
    try:
        result = _detector.detect(
            firstname=firstname,
            middlename=middlename,
            lastname=lastname,
        )
    except Exception:
        return "androgynous"

    if result == Gender.MALE:
        return "male"
    if result == Gender.FEMALE:
        return "female"
    return "androgynous"
