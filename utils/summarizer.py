import logging
import aiohttp

from config import Config

logger = logging.getLogger(__name__)

_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_TIMEOUT = aiohttp.ClientTimeout(total=15)
_MAX_CONTENT_CHARS = 6000

_PROMPT_TEMPLATE = (
    "다음 블로그 글을 한국어 존댓말로 3~4문장으로 핵심만 요약해 주세요.\n"
    "이모지나 마크다운 없이 평문으로만 작성해 주세요.\n\n"
    "제목: {title}\n"
    "본문:\n{content}"
)


async def summarize(title: str, content: str) -> str | None:
    """Gemini로 블로그 본문을 요약. 비활성/실패 시 None 반환 (재시도 없음)."""
    if not Config.GEMINI_API_KEY:
        return None

    content = (content or "").strip()
    if not content:
        return None

    if len(content) > _MAX_CONTENT_CHARS:
        content = content[:_MAX_CONTENT_CHARS]

    prompt = _PROMPT_TEMPLATE.format(title=title or "제목 없음", content=content)
    url = _ENDPOINT.format(model=Config.GEMINI_MODEL)
    headers = {
        "x-goog-api-key": Config.GEMINI_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            # maxOutputTokens는 thinking+출력 합산 예산(Gemini 3.x) → 넉넉히
            "maxOutputTokens": 2048,
            # 3.x는 thinking 완전 비활성 불가 → 최소 수준으로
            "thinkingConfig": {"thinkingLevel": "low"},
        },
    }

    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning("Gemini 요약 HTTP %d: %s", resp.status, body[:200])
                    return None
                data = await resp.json()

        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        return text or None
    except (aiohttp.ClientError, TimeoutError) as e:
        logger.warning("Gemini 요약 요청 실패: %s", e)
        return None
    except (KeyError, IndexError, TypeError) as e:
        finish = None
        try:
            finish = data["candidates"][0].get("finishReason")
        except Exception:
            pass
        logger.warning("Gemini 요약 응답 파싱 실패: %s (finishReason=%s)", e, finish)
        return None
    except Exception as e:
        logger.warning("Gemini 요약 알 수 없는 오류: %s", e)
        return None
