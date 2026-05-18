import logging
import asyncpg
from config import Config

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


async def init_db():
    global _pool
    _pool = await asyncpg.create_pool(Config.DATABASE_URL, min_size=2, max_size=10)
    async with _pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS members (
                id SERIAL PRIMARY KEY,
                guild_id TEXT NOT NULL,
                discord_id TEXT NOT NULL,
                discord_name TEXT NOT NULL,
                blog_url TEXT NOT NULL,
                rss_url TEXT NOT NULL,
                platform TEXT NOT NULL DEFAULT 'tistory',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(guild_id, discord_id, platform)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS posts (
                id SERIAL PRIMARY KEY,
                member_id INTEGER NOT NULL REFERENCES members(id),
                title TEXT NOT NULL,
                link TEXT NOT NULL,
                published_at TEXT,
                detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_initial INTEGER DEFAULT 0,
                UNIQUE(member_id, link)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS penalties (
                id SERIAL PRIMARY KEY,
                member_id INTEGER NOT NULL REFERENCES members(id),
                week_start TEXT NOT NULL,
                week_end TEXT NOT NULL,
                amount INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                guild_id TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                PRIMARY KEY(guild_id, key)
            )
        """)
    await _migrate_platform_if_needed()


async def _migrate_platform_if_needed():
    """platform 컬럼 없는 기존 DB에 컬럼 추가 및 UNIQUE 제약 변경 (최초 1회)"""
    async with _pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
            "WHERE table_name='members' AND column_name='platform')"
        )
        if exists:
            return

        await conn.execute("ALTER TABLE members ADD COLUMN platform TEXT NOT NULL DEFAULT 'tistory'")

        constraints = await conn.fetch(
            "SELECT constraint_name FROM information_schema.table_constraints "
            "WHERE table_name='members' AND constraint_type='UNIQUE'"
        )
        for c in constraints:
            await conn.execute(f"ALTER TABLE members DROP CONSTRAINT IF EXISTS {c['constraint_name']}")

        await conn.execute(
            "ALTER TABLE members ADD CONSTRAINT members_guild_discord_platform_key "
            "UNIQUE (guild_id, discord_id, platform)"
        )
        logger.info("members 테이블 platform 컬럼 마이그레이션 완료")


# ===== 멤버 CRUD =====

async def add_member(guild_id: str, discord_id: str, discord_name: str, blog_url: str, platform: str) -> bool:
    blog_url = blog_url.rstrip("/")
    if "velog.io/@" in blog_url:
        username = blog_url.split("/@")[1].rstrip("/")
        rss_url = f"https://v2.velog.io/rss/@{username}"
    else:
        rss_url = f"{blog_url}/rss"

    try:
        async with _pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO members (guild_id, discord_id, discord_name, blog_url, rss_url, platform) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                guild_id, discord_id, discord_name, blog_url, rss_url, platform
            )
            return True
    except asyncpg.UniqueViolationError:
        return False


async def remove_member(guild_id: str, discord_id: str, platform: str) -> bool:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM members WHERE guild_id = $1 AND discord_id = $2 AND platform = $3",
            guild_id, discord_id, platform
        )
        if not row:
            return False
        member_id = row["id"]
        await conn.execute("DELETE FROM posts WHERE member_id = $1", member_id)
        await conn.execute("DELETE FROM penalties WHERE member_id = $1", member_id)
        await conn.execute("DELETE FROM members WHERE id = $1", member_id)
        return True


async def get_all_members(guild_id: str = None) -> list[dict]:
    async with _pool.acquire() as conn:
        if guild_id:
            rows = await conn.fetch("SELECT * FROM members WHERE guild_id = $1", guild_id)
        else:
            rows = await conn.fetch("SELECT * FROM members")
        return [dict(row) for row in rows]


async def update_discord_name(member_id: int, new_name: str):
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE members SET discord_name = $1 WHERE id = $2",
            new_name, member_id
        )


async def get_member_by_discord_id(guild_id: str, discord_id: str, platform: str) -> dict | None:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM members WHERE guild_id = $1 AND discord_id = $2 AND platform = $3",
            guild_id, discord_id, platform
        )
        return dict(row) if row else None


async def get_members_by_discord_id(guild_id: str, discord_id: str) -> list[dict]:
    """해당 유저의 모든 플랫폼 멤버 항목 반환"""
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM members WHERE guild_id = $1 AND discord_id = $2",
            guild_id, discord_id
        )
        return [dict(row) for row in rows]


# ===== 포스팅 CRUD =====

async def is_post_exists(link: str, member_id: int) -> bool:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM posts WHERE link = $1 AND member_id = $2", link, member_id
        )
        return row is not None


async def add_post(member_id: int, title: str, link: str, published_at: str, is_initial: bool = False) -> bool:
    try:
        async with _pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO posts (member_id, title, link, published_at, is_initial) VALUES ($1, $2, $3, $4, $5)",
                member_id, title, link, published_at, 1 if is_initial else 0
            )
            return True
    except asyncpg.UniqueViolationError:
        return False


async def get_posts_in_range(member_id: int, start_date: str, end_date: str) -> list[dict]:
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM posts
               WHERE member_id = $1 AND published_at >= $2 AND published_at <= $3
               ORDER BY published_at DESC""",
            member_id, start_date, end_date
        )
        return [dict(row) for row in rows]


async def get_post_count_in_range(member_id: int, start_date: str, end_date: str) -> int:
    async with _pool.acquire() as conn:
        val = await conn.fetchval(
            """SELECT COUNT(*) FROM posts
               WHERE member_id = $1 AND published_at >= $2 AND published_at <= $3""",
            member_id, start_date, end_date
        )
        return val or 0


# ===== 벌금 CRUD =====

async def add_penalty(member_id: int, week_start: str, week_end: str, amount: int) -> bool:
    try:
        async with _pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO penalties (member_id, week_start, week_end, amount) VALUES ($1, $2, $3, $4)",
                member_id, week_start, week_end, amount
            )
            return True
    except Exception:
        return False


async def get_penalties_for_member(member_id: int) -> list[dict]:
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM penalties WHERE member_id = $1 ORDER BY week_start DESC",
            member_id
        )
        return [dict(row) for row in rows]


async def get_total_penalty(member_id: int) -> int:
    async with _pool.acquire() as conn:
        val = await conn.fetchval(
            "SELECT COALESCE(SUM(amount), 0) FROM penalties WHERE member_id = $1",
            member_id
        )
        return val or 0


async def is_penalty_exists(member_id: int, week_start: str) -> bool:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM penalties WHERE member_id = $1 AND week_start = $2",
            member_id, week_start
        )
        return row is not None


# ===== 설정 CRUD =====

async def get_setting(key: str, default: str = "", guild_id: str = None) -> str:
    async with _pool.acquire() as conn:
        if guild_id:
            val = await conn.fetchval(
                "SELECT value FROM settings WHERE guild_id = $1 AND key = $2", guild_id, key
            )
        else:
            val = await conn.fetchval(
                "SELECT value FROM settings WHERE key = $1 LIMIT 1", key
            )
        return val if val is not None else default


async def get_reset_time(guild_id: str) -> tuple[int, int, int]:
    weekday_str = await get_setting("reset_weekday", "0", guild_id)
    time_str = await get_setting("reset_time", "09:00", guild_id)

    try:
        weekday = int(weekday_str)
        hour, minute = map(int, time_str.split(":"))
        return weekday, hour, minute
    except ValueError:
        return 0, 9, 0


async def set_setting(guild_id: str, key: str, value: str):
    async with _pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO settings (guild_id, key, value) VALUES ($1, $2, $3)
               ON CONFLICT (guild_id, key) DO UPDATE SET value = EXCLUDED.value""",
            guild_id, key, value
        )


# ===== 유틸 =====

async def get_all_guild_ids() -> list[str]:
    async with _pool.acquire() as conn:
        rows = await conn.fetch("SELECT DISTINCT guild_id FROM members")
        return [row["guild_id"] for row in rows]


async def delete_all_guild_data(guild_id: str):
    async with _pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM posts WHERE member_id IN (SELECT id FROM members WHERE guild_id = $1)", guild_id
        )
        await conn.execute(
            "DELETE FROM penalties WHERE member_id IN (SELECT id FROM members WHERE guild_id = $1)", guild_id
        )
        await conn.execute("DELETE FROM members WHERE guild_id = $1", guild_id)
        await conn.execute("DELETE FROM settings WHERE guild_id = $1", guild_id)
