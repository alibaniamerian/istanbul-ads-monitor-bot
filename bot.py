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
from datetime import timezone as datetime_timezone

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
ISTANBUL_TIMEZONE = datetime_timezone(timedelta(hours=3), name="Europe/Istanbul")
DAILY_CALENDAR_HOUR = 7
PERSIAN_WEEKDAYS = ("دوشنبه", "سه‌شنبه", "چهارشنبه", "پنج‌شنبه", "جمعه", "شنبه", "یکشنبه")
TURKISH_WEEKDAYS = ("Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar")
TURKISH_MONTHS = (
    "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
    "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık",
)

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


def is_group_admin_status(status: str) -> bool:
    return status in {"administrator", "creator"}


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
        connection.execute(
            "CREATE TABLE IF NOT EXISTS daily_calendar (local_date TEXT PRIMARY KEY, sent_at TEXT NOT NULL)"
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS monitored_groups (
                chat_id INTEGER PRIMARY KEY,
                title TEXT
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS daily_group_calendar (
                local_date TEXT NOT NULL,
                chat_id INTEGER NOT NULL,
                sent_at TEXT NOT NULL,
                PRIMARY KEY(local_date, chat_id)
            )"""
        )
        connection.commit()


def calendar_sent(local_date: str) -> bool:
    with sqlite3.connect(DATABASE_PATH) as connection:
        return connection.execute(
            "SELECT 1 FROM daily_calendar WHERE local_date = ?", (local_date,)
        ).fetchone() is not None


def mark_calendar_sent(local_date: str) -> None:
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.execute(
            "INSERT OR IGNORE INTO daily_calendar(local_date, sent_at) VALUES (?, ?)",
            (local_date, datetime.now(timezone.utc).isoformat()),
        )
        connection.commit()


def register_group(message: Message) -> None:
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.execute(
            "INSERT OR REPLACE INTO monitored_groups(chat_id, title) VALUES (?, ?)",
            (message.chat.id, message.chat.title or str(message.chat.id)),
        )
        connection.commit()


def monitored_group_ids() -> list[int]:
    with sqlite3.connect(DATABASE_PATH) as connection:
        rows = connection.execute("SELECT chat_id FROM monitored_groups").fetchall()
        legacy_rows = connection.execute("SELECT DISTINCT chat_id FROM ads").fetchall()
    return sorted({row[0] for row in rows + legacy_rows})


def group_calendar_sent(local_date: str, chat_id: int) -> bool:
    with sqlite3.connect(DATABASE_PATH) as connection:
        return connection.execute(
            "SELECT 1 FROM daily_group_calendar WHERE local_date = ? AND chat_id = ?",
            (local_date, chat_id),
        ).fetchone() is not None


def mark_group_calendar_sent(local_date: str, chat_id: int) -> None:
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.execute(
            "INSERT OR IGNORE INTO daily_group_calendar(local_date, chat_id, sent_at) VALUES (?, ?, ?)",
            (local_date, chat_id, datetime.now(timezone.utc).isoformat()),
        )
        connection.commit()


def gregorian_to_jalali(year: int, month: int, day: int) -> tuple[int, int, int]:
    month_days = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    gy, gm, gd = year - 1600, month - 1, day - 1
    day_number = 365 * gy + (gy + 3) // 4 - (gy + 99) // 100 + (gy + 399) // 400
    day_number += sum(month_days[:gm]) + gd
    if gm > 1 and (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)):
        day_number += 1
    jalali_day_number = day_number - 79
    cycle = jalali_day_number // 12053
    jalali_day_number %= 12053
    jy = 979 + 33 * cycle + 4 * (jalali_day_number // 1461)
    jalali_day_number %= 1461
    if jalali_day_number >= 366:
        jy += (jalali_day_number - 1) // 365
        jalali_day_number = (jalali_day_number - 1) % 365
    if jalali_day_number < 186:
        jm = 1 + jalali_day_number // 31
        jd = 1 + jalali_day_number % 31
    else:
        jm = 7 + (jalali_day_number - 186) // 30
        jd = 1 + (jalali_day_number - 186) % 30
    return jy, jm, jd


def build_daily_calendar(now: datetime) -> str:
    local_date = now.date()
    jalali_year, jalali_month, jalali_day = gregorian_to_jalali(
        local_date.year, local_date.month, local_date.day
    )
    weekday = local_date.weekday()
    return (
        "╭──────────────╮\n"
        "│  📅 تقویم امروز  │\n"
        "╰──────────────╯\n\n"
        f"🇮🇷 شمسی: {PERSIAN_WEEKDAYS[weekday]}، {jalali_year}/{jalali_month:02d}/{jalali_day:02d}\n"
        f"🌍 میلادی: {now.strftime('%A')}، {local_date.year}/{local_date.month:02d}/{local_date.day:02d}\n"
        f"🇹🇷 ترکی: {TURKISH_WEEKDAYS[weekday]}، {local_date.day} {TURKISH_MONTHS[local_date.month - 1]} {local_date.year}\n\n"
        "🕖 ساعت محاسبه: به وقت استانبول"
    )


async def daily_calendar_loop(bot: Bot) -> None:
    while True:
        now = datetime.now(ISTANBUL_TIMEZONE)
        today = now.date().isoformat()
        if now.hour >= DAILY_CALENDAR_HOUR:
            try:
                message = build_daily_calendar(now)
                for group_id in monitored_group_ids():
                    if not group_calendar_sent(today, group_id):
                        await bot.send_message(group_id, message)
                        mark_group_calendar_sent(today, group_id)
                        logging.getLogger(__name__).info("Daily calendar sent to %s for %s", group_id, today)
            except Exception as error:
                logging.getLogger(__name__).warning("Daily calendar failed: %s", error)
            await asyncio.sleep(60)
            continue
        target = now.replace(hour=DAILY_CALENDAR_HOUR, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        await asyncio.sleep(max(30, (target - now).total_seconds()))


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


def build_correction_message(text: str, analysis: dict | None = None) -> str | None:
    price_found = has_price(text) or bool(analysis and analysis.get("has_price"))
    if analysis is not None and analysis.get("is_ad") is False:
        return None
    area_found = bool(find_area(text)) or bool(
        analysis and analysis.get("has_istanbul_location") and analysis.get("location")
    )
    missing = []
    if not price_found:
        missing.append("۱. قیمت دقیق کالا یا کالاها را به‌صورت خوانا درج کنید.")
    if not area_found:
        missing.append("۲. آدرس یا منطقه استانبول را به‌صورت خوانا درج کنید.")
    if not missing:
        return None
    return (
        "⚠️ لطفاً آگهی خود را اصلاح کنید:\n\n"
        + "\n".join(missing)
        + "\n\nمن یک ربات هستم و ممکن است آدرس و قیمت را درست تشخیص نداده باشم."
        + "\nلطفاً همین آگهی را طوری ویرایش کنید که اطلاعات را بهتر تشخیص بدهم؛ مثلاً:\n\n"
        + "آدرس: کادیکوی\nقیمت: ۲۰۰۰۰ لیر"
    )


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
    correction = build_correction_message(combined_text, analysis)
    if correction:
        await bot.send_message(
            representative.chat.id,
            correction,
            reply_to_message_id=representative.message_id,
        )


@router.message(CommandStart())
async def start(message: Message) -> None:
    if message.chat.type == "private":
        await message.answer("ربات فعال است و در حالت مشاهده‌گر هیچ پیامی را تغییر نمی‌دهد.")


@router.message()
async def inspect_message(message: Message, bot: Bot) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        return
    register_group(message)
    if message.from_user:
        member = await bot.get_chat_member(message.chat.id, message.from_user.id)
        if is_group_admin_status(member.status):
            logging.getLogger(__name__).info(
                "Skipping admin message %s from user %s",
                message.message_id,
                message.from_user.id,
            )
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
    asyncio.create_task(daily_calendar_loop(bot))
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
