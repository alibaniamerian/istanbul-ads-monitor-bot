import json
import logging
import os
from typing import Any

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
configured_model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
GEMINI_MODEL = (
    "gemini-3.6-flash"
    if configured_model == "gemini-2.0-flash"
    else configured_model
)
_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None


async def analyze_listing(text: str, images: list[tuple[bytes, str]]) -> dict[str, Any] | None:
    if _client is None:
        return None

    prompt = """این متن و تصاویر یک آگهی تلگرامی در گروه ایرانیان استانبول هستند.
متن ممکن است فارسی، ترکی، انگلیسی یا ترکیبی از آن‌ها باشد. فقط JSON معتبر برگردان و هیچ متن دیگری ننویس.
قیمت را فقط وقتی true کن که مبلغ واقعی کالا یا کالاها در متن یا تصویر دیده شود؛ شماره تلفن قیمت نیست.
آدرس را فقط وقتی استخراج کن که به استانبول یا یکی از مناطق/محله‌های آن مربوط باشد.
ساختار JSON دقیقاً این باشد:
{
  "is_ad": true,
  "has_price": true,
  "price_mentions": ["..."],
  "location": "...",
  "has_istanbul_location": true,
  "confidence": 0.0,
  "reason": "..."
}
اگر موردی پیدا نشد، مقدار متنی خالی و مقدار بولی false بده.

متن آگهی:
""" + text

    contents: list[Any] = [prompt]
    for data, mime_type in images:
        contents.append(types.Part.from_bytes(data=data, mime_type=mime_type))

    try:
        response = await _client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )
        result = json.loads(response.text)
        logger.info("Gemini analysis succeeded")
        return result if isinstance(result, dict) else None
    except Exception as error:
        logger.warning("Gemini analysis failed: %s", error)
        return None
