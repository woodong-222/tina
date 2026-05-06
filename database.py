import aiosqlite
from config import Config


async def init_db():
    async with aiosqlite.connect(Config.DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                discord_id TEXT NOT NULL,
                discord_name TEXT NOT NULL,
                blog_url TEXT NOT NULL,
                rss_url TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(guild_id, discord_id)
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                member_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                link TEXT UNIQUE NOT NULL,
                published_at DATETIME,
                detected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                is_initial INTEGER DEFAULT 0,
                FOREIGN KEY (member_id) REFERENCES members(id)
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS penalties (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                member_id INTEGER NOT NULL,
                week_start DATE NOT NULL,
                week_end DATE NOT NULL,
                amount INTEGER NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (member_id) REFERENCES members(id)
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                guild_id TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                PRIMARY KEY(guild_id, key)
            )
        """)

        await db.commit()


# ===== 멤버 CRUD =====

async def add_member(guild_id: str, discord_id: str, discord_name: str, blog_url: str) -> bool:
    blog_url = blog_url.rstrip("/")
    rss_url = f"{blog_url}/rss"

    try:
        async with aiosqlite.connect(Config.DB_PATH) as db:
            await db.execute(
                "INSERT INTO members (guild_id, discord_id, discord_name, blog_url, rss_url) VALUES (?, ?, ?, ?, ?)",
                (guild_id, discord_id, discord_name, blog_url, rss_url)
            )
            await db.commit()
            return True
    except aiosqlite.IntegrityError:
        return False


async def remove_member(guild_id: str, discord_id: str) -> bool:
    async with aiosqlite.connect(Config.DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id FROM members WHERE guild_id = ? AND discord_id = ?", (guild_id, discord_id)
        )
        row = await cursor.fetchone()
        if not row:
            return False
        member_id = row[0]
        await db.execute("DELETE FROM posts WHERE member_id = ?", (member_id,))
        await db.execute("DELETE FROM penalties WHERE member_id = ?", (member_id,))
        await db.execute("DELETE FROM members WHERE id = ?", (member_id,))
        await db.commit()
        return True


async def get_all_members(guild_id: str = None) -> list[dict]:
    async with aiosqlite.connect(Config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if guild_id:
            cursor = await db.execute("SELECT * FROM members WHERE guild_id = ?", (guild_id,))
        else:
            cursor = await db.execute("SELECT * FROM members")
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_member_by_discord_id(guild_id: str, discord_id: str) -> dict | None:
    async with aiosqlite.connect(Config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM members WHERE guild_id = ? AND discord_id = ?", (guild_id, discord_id)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


# ===== 포스팅 CRUD =====

async def is_post_exists(link: str) -> bool:
    async with aiosqlite.connect(Config.DB_PATH) as db:
        cursor = await db.execute(
            "SELECT 1 FROM posts WHERE link = ?", (link,)
        )
        return await cursor.fetchone() is not None


async def add_post(member_id: int, title: str, link: str, published_at: str, is_initial: bool = False) -> bool:
    """포스팅 추가. is_initial=True면 등록 시점의 기존 글 (알림 안 감)"""
    try:
        async with aiosqlite.connect(Config.DB_PATH) as db:
            await db.execute(
                "INSERT INTO posts (member_id, title, link, published_at, is_initial) VALUES (?, ?, ?, ?, ?)",
                (member_id, title, link, published_at, 1 if is_initial else 0)
            )
            await db.commit()
            return True
    except aiosqlite.IntegrityError:
        return False


async def get_posts_in_range(member_id: int, start_date: str, end_date: str) -> list[dict]:
    async with aiosqlite.connect(Config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT * FROM posts
               WHERE member_id = ? AND published_at >= ? AND published_at <= ?
               ORDER BY published_at DESC""",
            (member_id, start_date, end_date)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_post_count_in_range(member_id: int, start_date: str, end_date: str) -> int:
    async with aiosqlite.connect(Config.DB_PATH) as db:
        cursor = await db.execute(
            """SELECT COUNT(*) FROM posts
               WHERE member_id = ? AND published_at >= ? AND published_at <= ?""",
            (member_id, start_date, end_date)
        )
        row = await cursor.fetchone()
        return row[0] if row else 0


# ===== 벌금 CRUD =====

async def add_penalty(member_id: int, week_start: str, week_end: str, amount: int) -> bool:
    try:
        async with aiosqlite.connect(Config.DB_PATH) as db:
            await db.execute(
                "INSERT INTO penalties (member_id, week_start, week_end, amount) VALUES (?, ?, ?, ?)",
                (member_id, week_start, week_end, amount)
            )
            await db.commit()
            return True
    except Exception:
        return False


async def get_penalties_for_member(member_id: int) -> list[dict]:
    async with aiosqlite.connect(Config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM penalties WHERE member_id = ? ORDER BY week_start DESC",
            (member_id,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_total_penalty(member_id: int) -> int:
    async with aiosqlite.connect(Config.DB_PATH) as db:
        cursor = await db.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM penalties WHERE member_id = ?",
            (member_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else 0


async def is_penalty_exists(member_id: int, week_start: str) -> bool:
    async with aiosqlite.connect(Config.DB_PATH) as db:
        cursor = await db.execute(
            "SELECT 1 FROM penalties WHERE member_id = ? AND week_start = ?",
            (member_id, week_start)
        )
        return await cursor.fetchone() is not None


# ===== 설정 CRUD =====

async def get_setting(key: str, default: str = "", guild_id: str = None) -> str:
    async with aiosqlite.connect(Config.DB_PATH) as db:
        if guild_id:
            cursor = await db.execute(
                "SELECT value FROM settings WHERE guild_id = ? AND key = ?", (guild_id, key)
            )
        else:
            cursor = await db.execute(
                "SELECT value FROM settings WHERE key = ? LIMIT 1", (key,)
            )
        row = await cursor.fetchone()
        return row[0] if row else default


async def get_reset_time(guild_id: str) -> tuple[int, int, int]:
    """
    서버의 초기화 시간을 가져옵니다.
    반환: (weekday, hour, minute)
    기본값: 월요일(0), 09, 00
    """
    weekday_str = await get_setting("reset_weekday", "0", guild_id)
    time_str = await get_setting("reset_time", "09:00", guild_id)
    
    try:
        weekday = int(weekday_str)
        hour, minute = map(int, time_str.split(":"))
        return weekday, hour, minute
    except ValueError:
        return 0, 9, 0


async def set_setting(guild_id: str, key: str, value: str):
    async with aiosqlite.connect(Config.DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (guild_id, key, value) VALUES (?, ?, ?)",
            (guild_id, key, value)
        )
        await db.commit()


# ===== 유틸 =====

async def get_all_guild_ids() -> list[str]:
    """등록된 멤버가 있는 모든 길드 ID를 반환"""
    async with aiosqlite.connect(Config.DB_PATH) as db:
        cursor = await db.execute("SELECT DISTINCT guild_id FROM members")
        rows = await cursor.fetchall()
        return [row[0] for row in rows]
