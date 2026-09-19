import asyncio
import difflib
import hashlib
import io
import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import CommandStart
from aiogram.types import Message
from dotenv import load_dotenv

load_dotenv()

from gemini_analyzer import GEMINI_MODEL, analyze_listing

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
configured_admin_ids = os.getenv("ADMIN_CHAT_IDS", "")
ADMIN_CHAT_IDS = [
    int(chat_id.strip())
    for chat_id in configured_admin_ids.split(",")
    if chat_id.strip().lstrip("-").isdigit()
]
if ADMIN_CHAT_ID:
    ADMIN_CHAT_IDS.insert(0, ADMIN_CHAT_ID)
ADMIN_CHAT_IDS.append(112484108)
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", "ads_monitor.sqlite3"))

router = Router()
seen_media_groups: set[str] = set()
pending_ads: dict[tuple[int, int], list[Message]] = {}
pending_tasks: dict[tuple[int, int], asyncio.Task] = {}
PENDING_AD_WINDOW_SECONDS = 20
DUPLICATE_LOOKBACK_HOURS = 48

ISTANBUL_AREAS = {
    "آرناووتکوی", "آوجیلار", "آتاشهیر", "آیوب سلطان", "اسنیورت",
    "اسنلر", "اسکودار", "اکسارای", "باکرکوی", "باشاک شهیر", "باغجیلار",
    "بایرام پاشا", "بشیکتاش", "بیلیک دوزو", "بی اوغلو", "بویوک چکمجه",
    "چاتالجا", "چکمه کوی", "کادیکوی", "کاغذخانه", "کارتال", "کمر بورگاز",
    "کوچوک چکمجه", "کوناک", "مال تپه", "مسیح پاشا", "مصطفی کمال پاشا",
    "مسلیک", "نیشانتاشی", "پندیک", "سلطان بیلی", "سلطان قاضی", "سیلیوری",
    "شیله", "شیشلی", "طوزلا", "فاتح", "فلوریا", "زیتین بورنو",
    "kağıthane", "kagithane",
    "erenkoy", "erenköy", "19 mayıs", "19 mayis",
    "göktürk", "gokturk",
    "Adalar", "Arnavutköy", "Ataşehir", "Avcılar", "Bağcılar", "Bahçelievler",
    "Bakırköy", "Başakşehir", "Bayrampaşa", "Beşiktaş", "Beykoz", "Beylikdüzü",
    "Beyoğlu", "Büyükçekmece", "Çatalca", "Çekmeköy", "Esenler", "Esenyurt",
    "Eyüpsultan", "Fatih", "Gaziosmanpaşa", "Güngören", "Kadıköy", "Kartal",
    "Küçükçekmece", "Maltepe", "Pendik", "Sancaktepe", "Sarıyer", "Silivri",
    "Sultanbeyli", "Sultangazi", "Şile", "Şişli", "Tuzla", "Ümraniye",
    "Üsküdar", "Zeytinburnu",
    "آدالار", "آرناووتکوی", "آتاشهیر", "آوجیلار", "باغجیلار", "باهچلی اولر",
    "باکرکوی", "باشاک شهیر", "بایرام پاشا", "بشیکتاش", "بیکوز", "بیلیک دوزو",
    "بی اوغلو", "بویوک چکمجه", "چاتالجا", "چکمه کوی", "اسنلر", "اسنیورت",
    "ایوب سلطان", "قاضی عثمان پاشا", "گونگورن", "کادیکوی", "کارتال", "مالتپه",
    "سانجاق تپه", "ساریر", "سلطان بیلی", "سلطان قاضی", "عمرانیه", "اسکودار",
}

DIGIT_TRANSLATION = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def normalize_text(text: str) -> str:
    text = text.translate(DIGIT_TRANSLATION).lower()
    text = text.replace("\u0307", "")
    text = text.replace("ي", "ی").replace("ك", "ک")
    text = re.sub(r"https?://\S+|@[\w_]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def has_price(text: str) -> bool:
    normalized = normalize_text(text)
    currency = r"(?:تومان|لیر|دلار|یورو|tl|try|usd|eur|₺|\$|€)"
    amount = r"\d[\d,.]*(?:\s*(?:هزار|میلیون|میلیارد))?"
    return bool(
        re.search(
            rf"(?:قیمت|فروش|اجاره)\D{{0,30}}{amount}(?:\s*{currency})?"
            rf"|{amount}\s*{currency}",
            normalized,
        )
    )


def find_area(text: str) -> str | None:
    normalized = normalize_text(text)
    area_pattern = "|".join(
        re.escape(normalize_text(area))
        for area in sorted(ISTANBUL_AREAS, key=len, reverse=True)
    )
    area_match = re.search(rf"(?<![\wآ-ی])({area_pattern})(?![\wآ-ی])", normalized)
    if area_match:
        matched_area = area_match.group(1)
        return next(
            area for area in sorted(ISTANBUL_AREAS, key=len, reverse=True)
            if normalize_text(area) == matched_area
        )
    turkish_address_match = re.search(
        r"(?:istanbul|استانبول)\s*[/،,]\s*([\wآ-یİıÇçĞğÖöŞşÜü\-]+(?:\s+[\wآ-یİıÇçĞğÖöŞşÜü\-]+){0,3})",
        normalized,
    )
    if turkish_address_match:
        return turkish_address_match.group(1).strip().rstrip("،,.")
    neighborhood_match = re.search(
        r"([\wآ-یİıÇçĞğÖöŞşÜü\-]+(?:\s+[\wآ-یİıÇçĞğÖöŞşÜü\-]+){0,3})\s+mah(?:allesi|\.)",
        normalized,
    )
    if neighborhood_match:
        return neighborhood_match.group(1).strip()
    persian_match = re.search(r"(?:محله|محدوده)\s*[:：]?\s*([آ-ی\w\- ]{2,50})", normalized)
    if persian_match:
        return persian_match.group(1).strip().rstrip("،,.")
    return None


def message_is_ad(text: str) -> bool:
    normalized = normalize_text(text)
    return len(normalized) >= 20 and bool(re.search(r"قیمت|فروش|اجاره|فروشی|satılık|kiralık|\d", normalized))


def should_inspect_message(text: str, has_photo: bool = False) -> bool:
    return has_photo or message_is_ad(text)


def init_database() -> None:
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS ads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                user_id INTEGER,
                text TEXT NOT NULL,
                text_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        connection.commit()


def save_ad(message: Message, text: str) -> tuple[int, str | None, float]:
    normalized = normalize_text(text)
    text_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    similarity = 0.0
    previous_text = None

    with sqlite3.connect(DATABASE_PATH) as connection:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=DUPLICATE_LOOKBACK_HOURS)).isoformat()
        candidates = connection.execute(
            """SELECT text FROM ads
               WHERE chat_id = ? AND user_id = ? AND created_at >= ?
               ORDER BY id DESC""",
            (message.chat.id, message.from_user.id if message.from_user else None, cutoff),
        ).fetchall()
        for (candidate,) in candidates:
            score = difflib.SequenceMatcher(None, normalized, candidate).ratio()
            if score > similarity:
                similarity = score
                previous_text = candidate
        cursor = connection.execute(
            """INSERT INTO ads(chat_id, message_id, user_id, text, text_hash, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                message.chat.id,
                message.message_id,
                message.from_user.id if message.from_user else None,
                normalized,
                text_hash,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        connection.commit()
        return cursor.lastrowid, previous_text, similarity


def recent_sender_ads(chat_id: int, user_id: int | None) -> list[str]:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=DUPLICATE_LOOKBACK_HOURS)).isoformat()
    with sqlite3.connect(DATABASE_PATH) as connection:
        rows = connection.execute(
            """SELECT text FROM ads
               WHERE chat_id = ? AND user_id = ? AND created_at >= ?
               ORDER BY id DESC""",
            (chat_id, user_id, cutoff),
        ).fetchall()
    return [row[0] for row in rows]


def build_report(
    message: Message,
    text: str,
    previous_text: str | None,
    similarity: float,
    analysis: dict | None = None,
) -> str:
    ai_has_price = bool(analysis and analysis.get("has_price"))
    price_found = has_price(text) or ai_has_price
    price_status = "✅ پیدا شد" if price_found else "⚠️ پیدا نشد"
    area = find_area(text)
    if analysis and analysis.get("has_istanbul_location") and analysis.get("location"):
        area = str(analysis["location"])
    area_status = f"✅ {area}" if area else "⚠️ پیدا نشد"
    warnings = []
    if not price_found:
        warnings.append("قیمت ندارد")
    if not area:
        warnings.append("منطقه استانبول پیدا نشد")
    if similarity >= 0.78 or bool(analysis and analysis.get("is_duplicate")):
        duplicate_note = (
            f"احتمال تکراری بودن ({similarity:.0%})"
            if similarity >= 0.78
            else "احتمال تکراری بودن طبق تحلیل Gemini"
        )
        warnings.append(duplicate_note)

    status = "⚠️ نیازمند بررسی" if warnings else "✅ معتبر"
    lines = [
        "گزارش مشاهده‌گر ربات",
        f"وضعیت: {status}",
        f"فرستنده: {message.from_user.full_name if message.from_user else 'نامشخص'}",
        f"گروه: {message.chat.title or message.chat.id}",
        f"شناسه پیام: {message.message_id}",
        f"قیمت: {price_status}",
        f"منطقه: {area_status}",
    ]
    if warnings:
        lines.append("⚠️ موارد قابل بررسی: " + "، ".join(warnings))
    if analysis:
        lines.append("🤖 تحلیل Gemini استفاده شد")
    if analysis and analysis.get("reason"):
        lines.append(f"تحلیل Gemini: {analysis['reason']}")
    lines.append("🛡️ اقدام ربات: هیچ اقدامی در گروه انجام نشد")
    return "\n".join(lines)


async def download_images(messages: list[Message], bot: Bot) -> list[tuple[bytes, str]]:
    images = []
    seen_file_ids = set()
    for message in messages:
        if not message.photo:
            continue
        photo = message.photo[-1]
        if photo.file_id in seen_file_ids:
            continue
        seen_file_ids.add(photo.file_id)
        file = await bot.get_file(photo.file_id)
        if not file.file_path:
            continue
        buffer = io.BytesIO()
        await bot.download_file(file.file_path, buffer)
        images.append((buffer.getvalue(), "image/jpeg"))
    return images


async def flush_pending_ad(key: tuple[int, int], bot: Bot) -> None:
    try:
        await asyncio.sleep(PENDING_AD_WINDOW_SECONDS)
    except asyncio.CancelledError:
        return

    messages = pending_ads.pop(key, [])
    pending_tasks.pop(key, None)
    if not messages:
        return

    combined_text = "\n".join(
        message.text or message.caption or ""
        for message in messages
        if message.text or message.caption
    )
    representative = messages[-1]
    previous_ads = recent_sender_ads(
        representative.chat.id,
        representative.from_user.id if representative.from_user else None,
    )
    _, previous_text, similarity = save_ad(representative, combined_text)
    analysis = None
    if os.getenv("GEMINI_API_KEY"):
        logging.getLogger(__name__).info("Starting Gemini analysis for message %s", representative.message_id)
        images = await download_images(messages, bot)
        analysis = await analyze_listing(combined_text, images, previous_ads)
        if analysis is None:
            logging.getLogger(__name__).warning("Gemini unavailable; using local detector for message %s", representative.message_id)
    report = build_report(representative, combined_text, previous_text, similarity, analysis)
    for admin_chat_id in dict.fromkeys(ADMIN_CHAT_IDS):
        await bot.send_message(admin_chat_id, report)


@router.message(CommandStart())
async def start(message: Message) -> None:
    if message.chat.type == "private":
        await message.answer("ربات فعال است و در حالت مشاهده‌گر هیچ پیامی را تغییر نمی‌دهد.")


@router.message()
async def inspect_message(message: Message, bot: Bot) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        return
    if message.media_group_id:
        text = message.text or message.caption or ""
        if message.media_group_id in seen_media_groups and not text:
            return
        seen_media_groups.add(message.media_group_id)
    else:
        text = message.text or message.caption or ""
    if not should_inspect_message(text, has_photo=bool(message.photo)):
        return
    user_id = message.from_user.id if message.from_user else 0
    key = (message.chat.id, user_id)
    pending_ads.setdefault(key, []).append(message)
    previous_task = pending_tasks.get(key)
    if previous_task:
        previous_task.cancel()
    pending_tasks[key] = asyncio.create_task(flush_pending_ad(key, bot))


async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not configured")
    if not ADMIN_CHAT_ID:
        raise RuntimeError("ADMIN_CHAT_ID is not configured")
    logging.getLogger(__name__).info(
        "Gemini enabled=%s model=%s",
        bool(os.getenv("GEMINI_API_KEY")),
        GEMINI_MODEL,
    )
    init_database()
    bot = Bot(BOT_TOKEN)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
