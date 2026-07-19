import logging
from datetime import timedelta
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
                summary TEXT,
                tags TEXT,
                score INTEGER,
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
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS streaks (
                guild_id TEXT NOT NULL,
                discord_id TEXT NOT NULL,
                current INTEGER DEFAULT 0,
                best INTEGER DEFAULT 0,
                last_week TEXT,
                PRIMARY KEY(guild_id, discord_id)
            )
        """)
    await _migrate_platform_if_needed()
    await _migrate_settled_at_if_needed()
    await _migrate_post_ai_columns_if_needed()


async def _migrate_settled_at_if_needed():
    """settled_at 컬럼 없는 기존 DB에 컬럼 추가 (최초 1회)"""
    async with _pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
            "WHERE table_name='penalties' AND column_name='settled_at')"
        )
        if exists:
            return
        await conn.execute("ALTER TABLE penalties ADD COLUMN settled_at TIMESTAMP")
        logger.info("penalties 테이블 settled_at 컬럼 마이그레이션 완료")


async def _migrate_post_ai_columns_if_needed():
    """posts 테이블에 AI 요약/태그/점수 컬럼 추가 (최초 1회)"""
    async with _pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
            "WHERE table_name='posts' AND column_name='score')"
        )
        if exists:
            return
        await conn.execute("ALTER TABLE posts ADD COLUMN IF NOT EXISTS summary TEXT")
        await conn.execute("ALTER TABLE posts ADD COLUMN IF NOT EXISTS tags TEXT")
        await conn.execute("ALTER TABLE posts ADD COLUMN IF NOT EXISTS score INTEGER")
        logger.info("posts 테이블 AI 컬럼(summary/tags/score) 마이그레이션 완료")


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


async def add_post(member_id: int, title: str, link: str, published_at: str, is_initial: bool = False,
                   summary: str = None, tags: str = None, score: int = None) -> bool:
    try:
        async with _pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO posts (member_id, title, link, published_at, is_initial, summary, tags, score) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                member_id, title, link, published_at, 1 if is_initial else 0, summary, tags, score
            )
            return True
    except asyncpg.UniqueViolationError:
        return False


async def get_top_scored_post(guild_id: str, start_date: str, end_date: str) -> dict | None:
    """해당 guild 멤버들의 posts 중 기간 내 score 최고 1건(동점 시 최신). 점수 있는 글만."""
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT p.* FROM posts p
               JOIN members m ON p.member_id = m.id
               WHERE m.guild_id = $1
                 AND p.is_initial = 0
                 AND p.score IS NOT NULL
                 AND p.published_at >= $2 AND p.published_at <= $3
               ORDER BY p.score DESC, p.published_at DESC
               LIMIT 1""",
            guild_id, start_date, end_date
        )
        return dict(row) if row else None


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
            "SELECT COALESCE(SUM(amount), 0) FROM penalties WHERE member_id = $1 AND settled_at IS NULL",
            member_id
        )
        return val or 0


async def settle_penalties_for_guild(guild_id: str) -> int:
    """guild의 모든 미정산 벌금을 정산 처리. 정산된 레코드 수 반환"""
    async with _pool.acquire() as conn:
        result = await conn.execute(
            """UPDATE penalties SET settled_at = NOW()
               WHERE member_id IN (SELECT id FROM members WHERE guild_id = $1)
               AND settled_at IS NULL""",
            guild_id
        )
        return int(result.split()[-1])


async def reset_penalties_for_guild(guild_id: str) -> int:
    """guild의 모든 벌금 기록 완전 삭제. 삭제된 레코드 수 반환"""
    async with _pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM penalties WHERE member_id IN (SELECT id FROM members WHERE guild_id = $1)",
            guild_id
        )
        return int(result.split()[-1])


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


# ===== 스트릭 =====

async def get_streak(guild_id: str, discord_id: str) -> dict:
    """해당 유저의 스트릭. 없으면 기본값(current=0, best=0)."""
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT current, best, last_week FROM streaks WHERE guild_id = $1 AND discord_id = $2",
            guild_id, discord_id
        )
        if row:
            return dict(row)
        return {"current": 0, "best": 0, "last_week": None}


async def upsert_streak(guild_id: str, discord_id: str, current: int, best: int, last_week: str):
    async with _pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO streaks (guild_id, discord_id, current, best, last_week)
               VALUES ($1, $2, $3, $4, $5)
               ON CONFLICT (guild_id, discord_id)
               DO UPDATE SET current = EXCLUDED.current, best = EXCLUDED.best, last_week = EXCLUDED.last_week""",
            guild_id, discord_id, current, best, last_week
        )


# ===== 리더보드 (명예의 전당) =====

async def get_best_week_counts(guild_id: str) -> list[dict]:
    """discord_id별 '단일 주 최다 작성 편수'(is_initial=0). 주 경계는 guild 리셋 요일/시간 기준.
    편수>0만, 최고기록 내림차순. 동점은 discord_id로 결정적 정렬."""
    weekday, hour, minute = await get_reset_time(guild_id)
    # 리셋 순간이 월요일 00:00에 오도록 시프트할 오프셋 (date_trunc는 월요일 기준 주)
    offset = timedelta(days=weekday, hours=hour, minutes=minute)
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT w.discord_id,
                      MAX(mm.discord_name) AS discord_name,
                      MAX(w.cnt) AS post_count
               FROM (
                   SELECT m.discord_id AS discord_id,
                          date_trunc('week', p.published_at::timestamp - $2::interval) AS wk,
                          COUNT(*) AS cnt
                   FROM posts p
                   JOIN members m ON m.id = p.member_id
                   WHERE m.guild_id = $1
                     AND p.is_initial = 0
                     AND p.published_at IS NOT NULL
                   GROUP BY m.discord_id, date_trunc('week', p.published_at::timestamp - $2::interval)
               ) w
               JOIN members mm ON mm.discord_id = w.discord_id AND mm.guild_id = $1
               GROUP BY w.discord_id
               HAVING MAX(w.cnt) > 0
               ORDER BY MAX(w.cnt) DESC, w.discord_id ASC""",
            guild_id, offset
        )
        return [dict(row) for row in rows]


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
        await conn.execute("DELETE FROM streaks WHERE guild_id = $1", guild_id)
