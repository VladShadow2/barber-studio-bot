from datetime import date, datetime

START = (
    "Вітаємо. Це Barber Studio — демонстраційна стійка запису.\n\n"
    "Надішліть /start, коли захочете повернутися до цього повідомлення."
)

BTN_BOOK = "✂️ Записатися"
BTN_SERVICES = "📋 Послуги"
BTN_ABOUT = "🏠 Про салон"
BTN_BOOKINGS = "📅 Мої записи"
BTN_ADMIN = "⚙️ Адмін"
BTN_SCHEDULE = "📅 Мій розклад"
BTN_HOURS = "🕒 Мій графік"
BTN_BACK = "◀️ Назад"
BTN_CANCEL = "❌ Скасувати"
BTN_CONFIRM = "✅ Підтвердити"
BTN_SKIP = "⏭️ Пропустити"
BTN_RESUME = "▶️ Продовжити"
BTN_RESTART = "🔄 Почати спочатку"
BTN_ANY_MASTER = "👤 Будь-який вільний майстер"
BTN_YES_CANCEL = "✅ Так, скасувати"
BTN_NO = "❌ Ні"
BTN_ADMIN_HOME = "🏠 Адмін-меню"

NOT_AVAILABLE = "Ця дія недоступна."
STALE = "Ця кнопка застаріла. Ось поточний крок."
EXPIRED = "Цей крок запису закінчився. Натисніть «✂️ Записатися», щоб почати знову."
NO_SERVICES = "Зараз немає доступних послуг."
NO_MASTERS = "Зараз немає майстрів для цієї послуги."
NO_DAYS = "У доступному вікні немає вільних днів."
NO_SLOTS = "Цього дня немає вільного часу."
NO_BOOKINGS = "У вас немає майбутніх записів."
TAKEN = "Цей час щойно зайняли. Оберіть інший."
BOOKED = "✅ Ваш запис збережено."
CANCELLED = "❌ Запис скасовано."
ALREADY_CANCELLED = "Цей запис уже скасовано або завершено."
TOO_LATE = "До візиту залишилося замало часу, щоб скасувати його тут. Напишіть салону."
NOTE_PROMPT = "Надішліть коротку примітку для майстра або натисніть «Пропустити»."
USE_BUTTONS = "Скористайтеся кнопками в останньому повідомленні або надішліть /start."
UNFINISHED = "У вас є незавершений запис."
BOOKING_CANCELLED = "Запис скасовано."
CHOOSE_SERVICE = "💈 Оберіть послугу."
CHOOSE_MASTER = "👤 Оберіть майстра."
CHOOSE_DATE = "🗓️ Оберіть дату."
CHOOSE_TIME = "🕐 Оберіть час."
ANY_MASTER_LABEL = "Будь-який вільний майстер"
NOT_SELECTED = "не обрано"
CONFIRM_SUMMARY = "✅ Перевірте запис перед підтвердженням."
NOTE_PREFIX = "Примітка"
CANCEL_PROMPT = "Скасувати цей запис?"
UPCOMING = "📅 Майбутні записи"
OUR_SERVICES = "💈 Наші послуги"
OPENING_HOURS = "🕐 Години роботи"
CLOSED = "зачинено"
DEMO_BREAK = "У демонстраційному розкладі в робочі дні є перерва 14:00–15:00."

ABOUT_INTRO = (
    "Barber Studio — вигаданий салон для цього демонстраційного проєкту. "
    "Жоден реальний заклад цим ботом не користується.\n\n"
    "Адреса (вигадана): вул. Демонстраційна, 12, Київ\n"
    "Телефон (лише демо): +380 00 000 0000\n"
    "Електронна пошта (лише демо): hello@barber-studio.example\n"
)

ADMIN_HOME = "⚙️ Адміністрування Barber Studio. Оберіть розділ."
ASSIGN_SERVICES = "Оберіть послуги майстра. Зміни запишуться лише після «Зберегти»."
SERVICES_SAVED = "Послуги майстра збережено."
SERVICES_DISCARDED = "Зміни скасовано."
NO_SERVICES_TO_ASSIGN = "Немає послуг для призначення."
ADMIN_YES = "✅ Так, зберегти"
ADMIN_NO = "❌ Ні"
WEEKDAYS = ("Понеділок", "Вівторок", "Середа", "Четвер", "П'ятниця", "Субота", "Неділя")
WEEKDAY_SHORT = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд")

STATUS_LABELS = {
    "pending": "очікує",
    "confirmed": "підтверджено",
    "cancelled": "скасовано",
    "completed": "завершено",
}

_DOW = ("пн", "вт", "ср", "чт", "пт", "сб", "нд")
_MONTHS = (
    "січ",
    "лют",
    "бер",
    "квіт",
    "трав",
    "черв",
    "лип",
    "серп",
    "вер",
    "жовт",
    "лист",
    "груд",
)


def saved_hours_label(is_closed: bool | None, open_time, close_time) -> str:
    if is_closed is None and open_time is None and close_time is None:
        return "не збережено"
    if is_closed or open_time is None or close_time is None:
        return "закрито"
    return f"{open_time.strftime('%H:%M')}–{close_time.strftime('%H:%M')}"


def current_hours_text(weekday: int, row) -> str:
    if row is None:
        current = saved_hours_label(None, None, None)
    else:
        current = saved_hours_label(row.is_closed, row.open_time, row.close_time)
    return (
        f"📅 {WEEKDAYS[weekday]}\n\n"
        f"Поточний графік: {current}\n\n"
        "Введіть новий графік у форматі ГГ:ХХ-ГГ:ХХ або напишіть «закрито»."
    )


def saved_hours_text(weekday: int, closed: bool, open_time, close_time, *, master: bool) -> str:
    current = saved_hours_label(closed, open_time, close_time)
    title = "Графік майстра збережено" if master else "Графік збережено"
    return f"📅 {WEEKDAYS[weekday]}\n\n{title}: {current}"


def format_day(value: date | datetime) -> str:
    return f"{_DOW[value.weekday()]} {value.day} {_MONTHS[value.month - 1]}"


def format_when(value: datetime) -> str:
    return f"{format_day(value)} {value.year}, {value:%H:%M}"


def status_label(status: object) -> str:
    raw = getattr(status, "value", status)
    return STATUS_LABELS.get(str(raw), str(raw))
