import asyncio
import re
import logging
import aiohttp
import feedparser
import xml.etree.ElementTree as ET
from datetime import datetime

from utils.time_utils import get_kst_now
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


async def scan_and_save_existing_posts(member: dict, blog_url: str) -> int:
    """RSS + 사이트맵으로 기존 글을 스캔하여 is_initial=True로 저장. 처리된 글 수 반환."""
    blog_url = blog_url.rstrip("/")
    rss_url = f"{blog_url}/rss"
    sitemap_url = f"{blog_url}/sitemap.xml"
    added_links: set[str] = set()
    count = 0

    # feedparser는 동기 라이브러리이므로 이벤트 루프를 블로킹하지 않도록 executor에서 실행
    loop = asyncio.get_running_loop()
    feed = await loop.run_in_executor(None, feedparser.parse, rss_url)
    for entry in feed.entries:
        link = entry.get("link", "").strip()
        if not link or link in added_links:
            continue
        title = entry.get("title", "제목 없음").strip()
        try:
            published_dt = datetime(*entry.published_parsed[:6]) if entry.get("published_parsed") else get_kst_now()
            published_str = published_dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            published_str = get_kst_now().strftime("%Y-%m-%d %H:%M:%S")
        await db.add_post(member["id"], title, link, published_str, is_initial=True)
        added_links.add(link)
        count += 1

    # 사이트맵으로 RSS가 놓친 과거 글 보완
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(sitemap_url, timeout=10) as resp:
                if resp.status == 200:
                    xml_data = await resp.text()
                    root = ET.fromstring(xml_data)
                    urls = [elem.text for elem in root.findall('.//{http://www.sitemaps.org/schemas/sitemap/0.9}loc')]
                    post_urls = [
                        u for u in urls
                        if u
                        and not any(p in u for p in _IGNORE_PATTERNS)
                        and "/m/" not in u
                        and u != blog_url
                    ]
                    for link in post_urls:
                        if link in added_links:
                            continue
                        await db.add_post(
                            member["id"], "이전 글", link,
                            get_kst_now().strftime("%Y-%m-%d %H:%M:%S"),
                            is_initial=True
                        )
                        added_links.add(link)
                        count += 1
        except Exception as e:
            logger.error("사이트맵 스캔 실패 [%s]: %s", member.get("discord_name", "?"), e)

    return count
