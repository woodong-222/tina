import json
import logging
import aiohttp

from config import Config

logger = logging.getLogger(__name__)

_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_TIMEOUT = aiohttp.ClientTimeout(total=15)
_MAX_CONTENT_CHARS = 6000

_PROMPT_TEMPLATE = (
    "다음 블로그 글을 분석해서 JSON으로만 답해 주세요.\n"
    '- "summary": 한국어 존댓말 3~4문장 핵심 요약 (이모지/마크다운 없이 평문)\n'
    '- "tags": 주제 태그 2~3개 (짧은 한국어 단어 문자열 배열)\n'
    '- "score": 0~100 사이 정수 (글의 흥미도/완성도 체감 점수)\n\n'
    "제목: {title}\n"
    "본문:\n{content}"
)


async def summarize(title: str, content: str) -> dict | None:
    """Gemini로 블로그 본문을 분석. {'summary','tags','score'} 반환. 비활성/실패 시 None (재시도 없음)."""
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
            "responseMimeType": "application/json",
        },
    }

    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status != 200:
                    logger.warning("Gemini 분석 HTTP %d", resp.status)
                    return None
                data = await resp.json()

        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        return _parse_result(text)
    except (aiohttp.ClientError, TimeoutError) as e:
        logger.warning("Gemini 분석 요청 실패: %s", e)
        return None
    except (KeyError, IndexError, TypeError) as e:
        finish = None
        try:
            finish = data["candidates"][0].get("finishReason")
        except Exception:
            pass
        logger.warning("Gemini 분석 응답 파싱 실패: %s (finishReason=%s)", e, finish)
        return None
    except Exception as e:
        logger.warning("Gemini 분석 알 수 없는 오류: %s", e)
        return None


def _parse_result(text: str) -> dict | None:
    """모델 JSON 텍스트를 검증된 dict로 변환. 실패 시 None."""
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Gemini 분석 JSON 디코드 실패")
        return None

    if not isinstance(obj, dict):
        return None

    summary = obj.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return None
    summary = summary.strip()

    raw_tags = obj.get("tags")
    tags = []
    if isinstance(raw_tags, list):
        for t in raw_tags:
            if isinstance(t, str) and t.strip():
                tags.append(t.strip().lstrip("#"))
    tags = tags[:3]

    score = obj.get("score")
    try:
        score = int(score)
        score = max(0, min(100, score))
    except (TypeError, ValueError):
        score = None

    return {"summary": summary, "tags": tags, "score": score}
