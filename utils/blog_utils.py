import asyncio
import re
import logging
import aiohttp
import feedparser
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from utils.time_utils import get_kst_now, KST
import database as db

logger = logging.getLogger(__name__)

_IGNORE_PATTERNS = ('/category', '/tag', '/guestbook', '/manage')


def normalize_tistory_url(raw_url: str) -> str | None:
    """티스토리 URL을 정규화. 유효하지 않으면 None 반환."""
    match = re.search(r'(?:www\.)?([a-z0-9-]+)\.tistory\.com', raw_url.lower())
    if not match or match.group(1) == "www":
        return None
    return f"https://{match.group(1)}.tistory.com"


async def check_url_accessible(url: str) -> tuple[bool, int | None]:
    """URL 접근 가능 여부 확인. (성공여부, HTTP상태코드) 반환."""
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=10) as resp:
                return resp.status == 200, resp.status
        except Exception:
            return False, None


_PUB_DATE_PATTERNS = [
    r'<meta\s+property=["\']article:published_time["\']\s+content=["\']([^"\']+)["\']',
    r'<meta\s+content=["\']([^"\']+)["\']\s+property=["\']article:published_time["\']',
]


def parse_published_at_from_html(html: str) -> str | None:
    """HTML 문자열에서 article:published_time 메타 태그로 작성일을 파싱. 실패 시 None 반환."""
    for pattern in _PUB_DATE_PATTERNS:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            try:
                dt = datetime.fromisoformat(match.group(1))
                return dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass
    return None


async def fetch_post_published_at(session: aiohttp.ClientSession, url: str) -> str | None:
    """포스트 URL에서 실제 작성일(article:published_time)을 추출. 실패 시 None 반환."""
    try:
        async with session.get(url, timeout=5) as resp:
            if resp.status != 200:
                return None
            return parse_published_at_from_html(await resp.text())
    except Exception:
        pass
    return None


async def fetch_post_meta(session: aiohttp.ClientSession, url: str) -> tuple[str, str | None]:
    """포스트 URL에서 제목과 작성일을 한 번의 요청으로 추출. (title, published_at | None) 반환."""
    title = "이전 글"
    published_str = None
    try:
        async with session.get(url, timeout=5) as resp:
            if resp.status == 200:
                html = await resp.text()
                title_match = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', html, re.IGNORECASE)
                if title_match:
                    title = title_match.group(1).replace('&#39;', "'").replace('&quot;', '"')
                published_str = parse_published_at_from_html(html)
    except Exception:
        pass
    return title, published_str


async def scan_and_save_existing_posts(member: dict, blog_url: str) -> int:
    """RSS + 사이트맵으로 기존 글을 스캔하여 is_initial=True로 저장. 처리된 글 수 반환."""
    blog_url = blog_url.rstrip("/")
    rss_url = f"{blog_url}/rss"
    sitemap_url = f"{blog_url}/sitemap.xml"
    added_links: set[str] = set()
    count = 0

    logger.debug("기존 글 스캔 시작: [%s] %s", member.get("discord_name", "?"), blog_url)

    # feedparser는 동기 라이브러리이므로 이벤트 루프를 블로킹하지 않도록 executor에서 실행
    loop = asyncio.get_running_loop()
    feed = await loop.run_in_executor(None, feedparser.parse, rss_url)
    for entry in feed.entries:
        link = entry.get("link", "").strip()
        if not link or link in added_links:
            continue
        title = entry.get("title", "제목 없음").strip()
        try:
            if entry.get("published_parsed"):
                # feedparser는 항상 UTC로 반환 → KST로 변환 후 저장
                utc_dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                published_str = utc_dt.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S")
            else:
                published_str = get_kst_now().strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            published_str = get_kst_now().strftime("%Y-%m-%d %H:%M:%S")
        saved = await db.add_post(member["id"], title, link, published_str, is_initial=True)
        added_links.add(link)
        if saved:
            count += 1

    logger.debug("RSS 스캔 완료: [%s] %d편", member.get("discord_name", "?"), count)

    # 사이트맵으로 RSS가 놓친 과거 글 보완
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(sitemap_url, timeout=10) as resp:
                if resp.status == 200:
                    xml_data = await resp.text()
                    root = ET.fromstring(xml_data)
                    NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
                    for url_elem in root.findall(f"{NS}url"):
                        link = url_elem.findtext(f"{NS}loc")
                        if not link:
                            continue
                        if (
                            link in added_links
                            or any(p in link for p in _IGNORE_PATTERNS)
                            or "/m/" in link
                            or link == blog_url
                        ):
                            continue

                        # 페이지에서 제목과 실제 작성일 추출 (lastmod는 수정일이라 부정확)
                        title, published_str = await fetch_post_meta(session, link)
                        if not published_str:
                            lastmod = url_elem.findtext(f"{NS}lastmod")
                            try:
                                published_dt = datetime.fromisoformat(lastmod) if lastmod else get_kst_now()
                                published_str = published_dt.strftime("%Y-%m-%d %H:%M:%S")
                            except Exception:
                                published_str = get_kst_now().strftime("%Y-%m-%d %H:%M:%S")

                        saved = await db.add_post(
                            member["id"], title, link,
                            published_str,
                            is_initial=True
                        )
                        added_links.add(link)
                        if saved:
                            count += 1
        except Exception as e:
            logger.error("사이트맵 스캔 실패 [%s]: %s", member.get("discord_name", "?"), e)

    logger.debug("스캔 완료: [%s] 총 %d편 저장", member.get("discord_name", "?"), count)
    return count
