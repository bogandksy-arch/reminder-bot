"""
Telegram-бот для нагадувань.

Команди:
  /start                          — привітання
  /help                           — довідка
  /remind <час> <текст>           — створити нагадування
      Приклади часу:
        10m                → через 10 хвилин
        2h                 → через 2 години
        1d                 → через 1 день
        1d2h30m            → комбінація (1 день 2 год 30 хв)
        15:30              → сьогодні о 15:30 (або завтра, якщо вже минуло)
        25.12 10:00        → 25 грудня цього (або наступного) року о 10:00
        25.12.2026 10:00   → конкретна дата
  /list                           — список активних нагадувань
  /cancel <id>                    — скасувати нагадування за id

Запуск:
  1. pip install -r requirements.txt
  2. export TELEGRAM_BOT_TOKEN="ваш_токен_від_BotFather"
  3. python bot.py
"""

import json
import logging
import os
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

DATA_FILE = Path(__file__).parent / "reminders.json"

# ---------------------------------------------------------------------------
# Зберігання нагадувань у простому JSON-файлі
# ---------------------------------------------------------------------------

def load_reminders() -> dict:
    if DATA_FILE.exists():
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_reminders(data: dict) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Парсинг часу з тексту команди
# ---------------------------------------------------------------------------

RELATIVE_RE = re.compile(
    r"^(?:(?P<days>\d+)d)?(?:(?P<hours>\d+)h)?(?:(?P<minutes>\d+)m)?$"
)
TIME_ONLY_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
DATE_TIME_RE = re.compile(
    r"^(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?\s+(\d{1,2}):(\d{2})$"
)


def parse_when(token_time: str, token_date_time: str | None, now: datetime):
    """
    Пробує розпарсити час нагадування.
    Повертає (datetime, скільки_токенів_використано) або (None, 0).
    """
    # Відносний час: 10m, 2h, 1d2h30m
    if RELATIVE_RE.match(token_time) and token_time not in ("",):
        m = RELATIVE_RE.match(token_time)
        days = int(m.group("days") or 0)
        hours = int(m.group("hours") or 0)
        minutes = int(m.group("minutes") or 0)
        if days or hours or minutes:
            return now + timedelta(days=days, hours=hours, minutes=minutes), 1

    # Лише час: 15:30
    m = TIME_ONLY_RE.match(token_time)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate, 1

    # Дата + час, можливо як два окремі токени: "25.12" "10:00"
    if token_date_time:
        combined = f"{token_time} {token_date_time}"
        m = DATE_TIME_RE.match(combined)
        if m:
            day, month, year, hour, minute = m.groups()
            year = int(year) if year else now.year
            try:
                candidate = datetime(year, int(month), int(day), int(hour), int(minute))
            except ValueError:
                return None, 0
            if candidate <= now and not m.group(3):
                candidate = candidate.replace(year=year + 1)
            return candidate, 2

    return None, 0


# ---------------------------------------------------------------------------
# Логіка нагадувань
# ---------------------------------------------------------------------------

async def send_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    job = context.job
    chat_id = job.chat_id
    reminder_id = job.data["id"]
    text = job.data["text"]

    await context.bot.send_message(
        chat_id=chat_id,
        text=f"🔔 Нагадування: {text}",
    )

    data = load_reminders()
    chat_key = str(chat_id)
    if chat_key in data and reminder_id in data[chat_key]:
        del data[chat_key][reminder_id]
        if not data[chat_key]:
            del data[chat_key]
        save_reminders(data)


def schedule_job(app: Application, chat_id: int, reminder_id: str, text: str, when: datetime) -> None:
    app.job_queue.run_once(
        send_reminder,
        when=when,
        chat_id=chat_id,
        data={"id": reminder_id, "text": text},
        name=f"{chat_id}:{reminder_id}",
    )


# ---------------------------------------------------------------------------
# Обробники команд
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "Привіт! Я нагадую про справи.\n\n"
        "Приклади:\n"
        "/remind 10m Купити молоко\n"
        "/remind 15:30 Зустріч\n"
        "/remind 25.12 10:00 Привітати з Різдвом\n\n"
        "Команди: /list — активні нагадування, /cancel <id> — скасувати, /help — довідка."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "Формати часу для /remind:\n"
        "  10m           — через 10 хвилин\n"
        "  2h            — через 2 години\n"
        "  1d            — через 1 день\n"
        "  1d2h30m       — комбінація\n"
        "  15:30         — сьогодні (або завтра) о 15:30\n"
        "  25.12 10:00   — 25 грудня о 10:00\n"
        "  25.12.2026 10:00 — конкретна дата й рік\n\n"
        "Приклад: /remind 2h30m Зателефонувати мамі\n\n"
        "/list — показати активні нагадування\n"
        "/cancel <id> — скасувати нагадування"
    )


async def remind(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.effective_message.reply_text(
            "Вкажіть час і текст, наприклад:\n/remind 10m Купити молоко"
        )
        return

    now = datetime.now()
    args = context.args
    token_time = args[0]
    token_second = args[1] if len(args) > 1 else None

    when, used_tokens = parse_when(token_time, token_second, now)

    if when is None:
        await update.effective_message.reply_text(
            "Не вдалося розпізнати час. Приклади: 10m, 2h, 15:30, 25.12 10:00.\n"
            "Введіть /help для повного списку форматів."
        )
        return

    text_parts = args[used_tokens:]
    if not text_parts:
        await update.effective_message.reply_text("Додайте текст нагадування після часу.")
        return

    text = " ".join(text_parts)
    reminder_id = uuid.uuid4().hex[:6]
    chat_id = update.effective_chat.id

    data = load_reminders()
    chat_key = str(chat_id)
    data.setdefault(chat_key, {})
    data[chat_key][reminder_id] = {
        "text": text,
        "run_at": when.isoformat(),
    }
    save_reminders(data)

    schedule_job(context.application, chat_id, reminder_id, text, when)

    await update.effective_message.reply_text(
        f"Готово ✅ Нагадаю {when.strftime('%d.%m.%Y о %H:%M')}\n"
        f"«{text}»\n"
        f"id: {reminder_id}"
    )


async def list_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    data = load_reminders()
    chat_reminders = data.get(str(chat_id), {})

    if not chat_reminders:
        await update.effective_message.reply_text("Активних нагадувань немає.")
        return

    items = sorted(chat_reminders.items(), key=lambda kv: kv[1]["run_at"])
    lines = ["Активні нагадування:"]
    for rid, info in items:
        when = datetime.fromisoformat(info["run_at"])
        lines.append(f"• [{rid}] {when.strftime('%d.%m %H:%M')} — {info['text']}")
    lines.append("\nСкасувати: /cancel <id>")

    await update.effective_message.reply_text("\n".join(lines))


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.effective_message.reply_text("Вкажіть id нагадування: /cancel <id>")
        return

    reminder_id = context.args[0]
    chat_id = update.effective_chat.id
    chat_key = str(chat_id)

    data = load_reminders()
    if chat_key not in data or reminder_id not in data[chat_key]:
        await update.effective_message.reply_text("Нагадування з таким id не знайдено.")
        return

    del data[chat_key][reminder_id]
    if not data[chat_key]:
        del data[chat_key]
    save_reminders(data)

    current_jobs = context.application.job_queue.get_jobs_by_name(f"{chat_id}:{reminder_id}")
    for job in current_jobs:
        job.schedule_removal()

    await update.effective_message.reply_text("Нагадування скасовано.")


# ---------------------------------------------------------------------------
# Відновлення нагадувань після перезапуску
# ---------------------------------------------------------------------------

async def reschedule_all(app: Application) -> None:
    data = load_reminders()
    now = datetime.now()
    changed = False

    for chat_key, reminders in list(data.items()):
        chat_id = int(chat_key)
        for reminder_id, info in list(reminders.items()):
            when = datetime.fromisoformat(info["run_at"])
            if when <= now:
                # пропущене нагадування — видаляємо, щоб не спамити старими
                del reminders[reminder_id]
                changed = True
                continue
            schedule_job(app, chat_id, reminder_id, info["text"], when)
        if not reminders:
            del data[chat_key]
            changed = True

    if changed:
        save_reminders(data)


async def post_init(app: Application) -> None:
    await reschedule_all(app)
    logger.info("Нагадування відновлено після запуску.")


# ---------------------------------------------------------------------------
# Точка входу
# ---------------------------------------------------------------------------

def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit(
            "Не знайдено TELEGRAM_BOT_TOKEN. "
            "Встановіть змінну середовища перед запуском:\n"
            "  export TELEGRAM_BOT_TOKEN=\"ваш_токен\""
        )

    app = Application.builder().token(token).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("remind", remind))
    app.add_handler(CommandHandler("list", list_reminders))
    app.add_handler(CommandHandler("cancel", cancel))

    logger.info("Бот запущено.")
    app.run_polling()


if __name__ == "__main__":
    main()
