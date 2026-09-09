"""Guild-isolated daily administrator points for the BT and LN servers."""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

KUWAIT_TZ = ZoneInfo("Asia/Kuwait")
DATABASE_FILE = Path(__file__).resolve().parents[2] / "admin_points.db"

BT_GUILD_ID = 1487869417256915148
LN_GUILD_ID = 1502928734477353011
BT_DAILY_POINTS_CHANNEL_ID = 1544887081447194734
LN_DAILY_POINTS_CHANNEL_ID = 1544903884806299740
DAILY_REPORT_HOUR = 0
DAILY_REPORT_MINUTE = 0

BT_IMAGE_CHANNEL_IDS = {
    1487874181377818686,
    1487874808107503800,
}
BT_VIDEO_CHANNEL_IDS = {
    1487874936692277499,
    1487874982347411557,
}

POINT_RANKS = [
    ("Junior", 0),
    ("High Junior", 200),
    ("Admin", 500),
    ("Super Admin", 900),
    ("High Admin", 1400),
    ("Admin Staff", 2000),
]
MANUAL_RANKS = [
    "High Staff",
    "Colonel",
    "High Colonel",
    "Co Manager",
    "Manager Staff",
    "Leader",
    "Co Founder",
    "Founder",
]
ALL_RANKS = [name for name, _ in POINT_RANKS] + MANUAL_RANKS

BT_ROLE_NAMES = {
    "Junior": "ᵇᵀ 𝐉𝐔𝐍𝐈𝐎𝐑",
    "High Junior": "ᵇᵀ 𝐇𝐈𝐆𝐇 𝐉𝐔𝐍𝐈𝐎𝐑",
    "Admin": "ᵇᵀ 𝐀𝐃𝐌𝐈𝐍",
    "Super Admin": "ᵇᵀ 𝐒𝐔𝐏𝐄𝐑 𝐀𝐃𝐌𝐈𝐍",
    "High Admin": "ᵇᵀ 𝐇𝐈𝐆𝐇 𝐀𝐃𝐌𝐈𝐍",
    "Admin Staff": "ᵇᵀ 𝐀𝐃𝐌𝐈𝐍 𝐒𝐓𝐀𝐅𝐅",
    "High Staff": "ᵇᵀ 𝐇𝐈𝐆𝐇 𝐒𝐓𝐀𝐅𝐅",
    "Colonel": "ᵇᵀ 𝐂𝐎𝐋𝐎𝐍𝐄𝐋",
    "High Colonel": "ᵇᵀ 𝐇𝐈𝐆𝐇 𝐂𝐎𝐋𝐎𝐍𝐄𝐋",
    "Co Manager": "ᵇᵀ 𝐂𝐎 𝐌𝐀𝐍𝐀𝐆𝐄𝐑",
    "Manager Staff": "ᵇᵀ 𝐒𝐓𝐀𝐅𝐅 𝐌𝐀𝐍𝐀𝐆𝐄𝐑",
    "Leader": "ᵇᵀ 𝐋𝐄𝐀𝐃𝐄𝐑",
    "Co Founder": "ᵇᵀ 𝐂𝐎 𝐅𝐎𝐔𝐍𝐃𝐄𝐑",
    "Founder": "ᵇᵀ 𝐅𝐎𝐔𝐍𝐃𝐄𝐑",
}
LN_ROLE_NAMES = {
    "Junior": "Junior ᴸᴺ",
    "High Junior": "High Junior ᴸᴺ",
    "Admin": "Admin ᴸᴺ",
    "Super Admin": "Super Admin ᴸᴺ",
    "High Admin": "High Admin ᴸᴺ",
    "Admin Staff": "Admin Staff ᴸᴺ",
    "High Staff": "High Staff ᴸᴺ",
    "Colonel": "Colonel ᴸᴺ",
    "High Colonel": "High Colonel ᴸᴺ",
    "Co Manager": "Co Manager ᴸᴺ",
    "Manager Staff": "Manager Staff ᴸᴺ",
    "Leader": "Leader ᴸᴺ",
    "Co Founder": "Co Founder ᴸᴺ",
    "Founder": "Founder ᴸᴺ",
}
BT_ROLE_IDS = {
    "Junior": 1544884563585794099,
    "High Junior": 1544884910186045540,
    "Admin": 1544885042054963210,
    "Super Admin": 1544885208866758666,
    "High Admin": 1544885592758816809,
    "Admin Staff": 1544885732877926431,
    "High Staff": 1544886176291229768,
    "Colonel": 1544886359452549200,
    "High Colonel": 1544886490537140284,
    "Co Manager": 1544886713975832586,
    "Manager Staff": 1544887055555764334,
    "Leader": 1544887525582049300,
    "Co Founder": 1544887701465997442,
    "Founder": 1544887982249476197,
}
LN_ROLE_IDS = {
    "Junior": 1544887364965507152,
    "High Junior": 1544887819573526668,
    "Admin": 1544888235535368294,
    "Super Admin": 1544889040518643882,
    "High Admin": 1544889241996099694,
    "Admin Staff": 1544889365707362364,
    "High Staff": 1544889552743694468,
    "Colonel": 1544889710298660864,
    "High Colonel": 1544889863084707890,
    "Co Manager": 1544890012951117904,
    "Manager Staff": 1544890160418922547,
    "Leader": 1544890325020180530,
    "Co Founder": 1544890468704325672,
    "Founder": 1544890594571194498,
}

REPORT_CUSTOM_ID = "leon_admin_points:confirm"
SOURCE_COLUMNS = {
    "tickets": "ticket_points",
    "events": "event_points",
    "messages": "message_points",
    "images": "image_points",
    "videos": "video_points",
}
COUNTER_CONFIG = {
    "messages": ("message_count", 100, 1000),
    "images": ("image_count", 4, None),
    "videos": ("video_count", 9, None),
}


def current_day() -> str:
    return datetime.now(KUWAIT_TZ).strftime("%Y-%m-%d")


def supported_guild_ids() -> tuple[int, ...]:
    return tuple(guild_id for guild_id in (BT_GUILD_ID, LN_GUILD_ID) if guild_id)


def is_supported_guild(guild_id: int) -> bool:
    return guild_id != 0 and guild_id in supported_guild_ids()


def role_ids_for(guild_id: int) -> dict[str, int]:
    if guild_id == BT_GUILD_ID and BT_GUILD_ID:
        return BT_ROLE_IDS
    if guild_id == LN_GUILD_ID and LN_GUILD_ID:
        return LN_ROLE_IDS
    return {}


def report_channel_id_for(guild_id: int) -> int:
    if guild_id == BT_GUILD_ID and BT_GUILD_ID:
        return BT_DAILY_POINTS_CHANNEL_ID
    if guild_id == LN_GUILD_ID and LN_GUILD_ID:
        return LN_DAILY_POINTS_CHANNEL_ID
    return 0


def is_current_admin(member: discord.Member) -> bool:
    valid_ids = {role_id for role_id in role_ids_for(member.guild.id).values() if role_id}
    return bool(valid_ids) and any(role.id in valid_ids for role in member.roles)


def current_rank(member: discord.Member) -> str | None:
    role_ids = role_ids_for(member.guild.id)
    owned = [
        (index, rank)
        for index, rank in enumerate(ALL_RANKS)
        if role_ids.get(rank)
        and any(role.id == role_ids[rank] for role in member.roles)
    ]
    return max(owned, default=(-1, None), key=lambda item: item[0])[1]


def rank_from_points(points: int) -> str:
    result = POINT_RANKS[0][0]
    for name, required in POINT_RANKS:
        if points >= required:
            result = name
    return result


def next_rank(points: int) -> tuple[str, int, int] | None:
    for name, required in POINT_RANKS:
        if points < required:
            return name, required, required - points
    return None


class AdminPointsDatabase:
    """Short-lived SQLite connections with cross-process-safe transactions."""

    def __init__(self, path: Path = DATABASE_FILE) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def initialize(self) -> None:
        connection = self.connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS admin_points (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    total_points INTEGER NOT NULL DEFAULT 0,
                    ticket_points INTEGER NOT NULL DEFAULT 0,
                    event_points INTEGER NOT NULL DEFAULT 0,
                    message_points INTEGER NOT NULL DEFAULT 0,
                    image_points INTEGER NOT NULL DEFAULT 0,
                    video_points INTEGER NOT NULL DEFAULT 0,
                    current_rank TEXT,
                    PRIMARY KEY (guild_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS daily_counters (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    day TEXT NOT NULL,
                    message_count INTEGER NOT NULL DEFAULT 0,
                    image_count INTEGER NOT NULL DEFAULT 0,
                    video_count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (guild_id, user_id, day)
                );
                CREATE TABLE IF NOT EXISTS daily_points (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    day TEXT NOT NULL,
                    total_points INTEGER NOT NULL DEFAULT 0,
                    ticket_points INTEGER NOT NULL DEFAULT 0,
                    event_points INTEGER NOT NULL DEFAULT 0,
                    message_points INTEGER NOT NULL DEFAULT 0,
                    image_points INTEGER NOT NULL DEFAULT 0,
                    video_points INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'pending',
                    confirmed_by INTEGER,
                    confirmed_at TEXT,
                    PRIMARY KEY (guild_id, user_id, day)
                );
                CREATE TABLE IF NOT EXISTS counted_activities (
                    activity_id TEXT PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    activity_type TEXT NOT NULL,
                    day TEXT NOT NULL,
                    points INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS daily_reports (
                    guild_id INTEGER NOT NULL,
                    day TEXT NOT NULL,
                    message_id INTEGER,
                    status TEXT NOT NULL DEFAULT 'pending',
                    confirmed_by INTEGER,
                    confirmed_at TEXT,
                    PRIMARY KEY (guild_id, day)
                );
                """
            )
            expected = {
                "admin_points": {
                    "ticket_points": "INTEGER NOT NULL DEFAULT 0",
                    "event_points": "INTEGER NOT NULL DEFAULT 0",
                    "message_points": "INTEGER NOT NULL DEFAULT 0",
                    "image_points": "INTEGER NOT NULL DEFAULT 0",
                    "video_points": "INTEGER NOT NULL DEFAULT 0",
                    "current_rank": "TEXT",
                },
                "daily_counters": {
                    "message_count": "INTEGER NOT NULL DEFAULT 0",
                    "image_count": "INTEGER NOT NULL DEFAULT 0",
                    "video_count": "INTEGER NOT NULL DEFAULT 0",
                },
                "daily_points": {
                    "ticket_points": "INTEGER NOT NULL DEFAULT 0",
                    "event_points": "INTEGER NOT NULL DEFAULT 0",
                    "message_points": "INTEGER NOT NULL DEFAULT 0",
                    "image_points": "INTEGER NOT NULL DEFAULT 0",
                    "video_points": "INTEGER NOT NULL DEFAULT 0",
                    "status": "TEXT NOT NULL DEFAULT 'pending'",
                    "confirmed_by": "INTEGER",
                    "confirmed_at": "TEXT",
                },
                "daily_reports": {
                    "message_id": "INTEGER",
                    "status": "TEXT NOT NULL DEFAULT 'pending'",
                    "confirmed_by": "INTEGER",
                    "confirmed_at": "TEXT",
                },
            }
            for table, columns in expected.items():
                existing = {
                    row["name"]
                    for row in connection.execute(f"PRAGMA table_info({table})")
                }
                for name, definition in columns.items():
                    if name not in existing:
                        connection.execute(
                            f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
                        )
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _ensure_rows(
        connection: sqlite3.Connection,
        guild_id: int,
        user_id: int,
        day: str,
    ) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO admin_points (guild_id, user_id) VALUES (?, ?)",
            (guild_id, user_id),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO daily_points (guild_id, user_id, day)
            VALUES (?, ?, ?)
            """,
            (guild_id, user_id, day),
        )

    def add_points(
        self,
        guild_id: int,
        user_id: int,
        day: str,
        points: int,
        source: str,
        activity_id: str,
    ) -> bool:
        column = SOURCE_COLUMNS.get(source)
        if column is None or points <= 0:
            return False
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_rows(connection, guild_id, user_id, day)
            activity = connection.execute(
                """
                INSERT OR IGNORE INTO counted_activities (
                    activity_id, guild_id, user_id, activity_type,
                    day, points, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    activity_id,
                    guild_id,
                    user_id,
                    source,
                    day,
                    points,
                    datetime.now(KUWAIT_TZ).isoformat(),
                ),
            )
            if activity.rowcount != 1:
                connection.rollback()
                return False
            updated = connection.execute(
                f"""
                UPDATE daily_points
                SET total_points = total_points + ?, {column} = {column} + ?
                WHERE guild_id = ? AND user_id = ? AND day = ?
                  AND status = 'pending'
                """,
                (points, points, guild_id, user_id, day),
            )
            if updated.rowcount != 1:
                connection.rollback()
                return False
            connection.commit()
            return True
        finally:
            connection.close()

    def count_activity(
        self,
        guild_id: int,
        user_id: int,
        day: str,
        source: str,
        activity_id: str,
    ) -> bool:
        counter_column, bucket_size, daily_max = COUNTER_CONFIG[source]
        points_column = SOURCE_COLUMNS[source]
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_rows(connection, guild_id, user_id, day)
            connection.execute(
                """
                INSERT OR IGNORE INTO daily_counters (guild_id, user_id, day)
                VALUES (?, ?, ?)
                """,
                (guild_id, user_id, day),
            )
            activity = connection.execute(
                """
                INSERT OR IGNORE INTO counted_activities (
                    activity_id, guild_id, user_id, activity_type,
                    day, points, created_at
                ) VALUES (?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    activity_id,
                    guild_id,
                    user_id,
                    source,
                    day,
                    datetime.now(KUWAIT_TZ).isoformat(),
                ),
            )
            if activity.rowcount != 1:
                connection.rollback()
                return False
            row = connection.execute(
                f"""
                SELECT {counter_column} AS count FROM daily_counters
                WHERE guild_id = ? AND user_id = ? AND day = ?
                """,
                (guild_id, user_id, day),
            ).fetchone()
            old_count = int(row["count"])
            if daily_max is not None and old_count >= daily_max:
                connection.rollback()
                return False
            new_count = old_count + 1
            earned = new_count // bucket_size - old_count // bucket_size
            connection.execute(
                f"""
                UPDATE daily_counters SET {counter_column} = ?
                WHERE guild_id = ? AND user_id = ? AND day = ?
                """,
                (new_count, guild_id, user_id, day),
            )
            if earned:
                updated = connection.execute(
                    f"""
                    UPDATE daily_points
                    SET total_points = total_points + ?,
                        {points_column} = {points_column} + ?
                    WHERE guild_id = ? AND user_id = ? AND day = ?
                      AND status = 'pending'
                    """,
                    (earned, earned, guild_id, user_id, day),
                )
                if updated.rowcount != 1:
                    connection.rollback()
                    return False
                connection.execute(
                    "UPDATE counted_activities SET points = ? WHERE activity_id = ?",
                    (earned, activity_id),
                )
            connection.commit()
            return True
        finally:
            connection.close()

    def reserve_report(self, guild_id: int, day: str) -> sqlite3.Row:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT OR IGNORE INTO daily_reports (guild_id, day, status)
                VALUES (?, ?, 'pending')
                """,
                (guild_id, day),
            )
            row = connection.execute(
                "SELECT * FROM daily_reports WHERE guild_id = ? AND day = ?",
                (guild_id, day),
            ).fetchone()
            connection.commit()
            if row is None:
                raise RuntimeError("Could not reserve daily report.")
            return row
        finally:
            connection.close()

    def set_report_message(self, guild_id: int, day: str, message_id: int) -> None:
        connection = self.connect()
        try:
            connection.execute(
                """
                UPDATE daily_reports SET message_id = ?
                WHERE guild_id = ? AND day = ? AND status = 'pending'
                  AND message_id IS NULL
                """,
                (message_id, guild_id, day),
            )
            connection.commit()
        finally:
            connection.close()

    def pending_days(self, guild_id: int) -> list[str]:
        connection = self.connect()
        try:
            return [
                row["day"]
                for row in connection.execute(
                    """
                    SELECT DISTINCT day FROM daily_points
                    WHERE guild_id = ? AND status = 'pending'
                      AND total_points > 0 AND day < ?
                    ORDER BY day
                    """,
                    (guild_id, current_day()),
                )
            ]
        finally:
            connection.close()

    def daily_rows(self, guild_id: int, day: str) -> list[sqlite3.Row]:
        connection = self.connect()
        try:
            return list(
                connection.execute(
                    """
                    SELECT * FROM daily_points
                    WHERE guild_id = ? AND day = ? AND status = 'pending'
                      AND total_points > 0
                    ORDER BY total_points DESC, user_id
                    """,
                    (guild_id, day),
                )
            )
        finally:
            connection.close()

    def report_for_message(self, guild_id: int, message_id: int) -> sqlite3.Row | None:
        connection = self.connect()
        try:
            return connection.execute(
                """
                SELECT * FROM daily_reports
                WHERE guild_id = ? AND message_id = ?
                """,
                (guild_id, message_id),
            ).fetchone()
        finally:
            connection.close()

    def approve_day(
        self,
        guild_id: int,
        day: str,
        confirmer_id: int,
    ) -> tuple[bool, str, list[int]]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            now = datetime.now(KUWAIT_TZ).isoformat()
            claimed = connection.execute(
                """
                UPDATE daily_reports
                SET status = 'confirming', confirmed_by = ?, confirmed_at = ?
                WHERE guild_id = ? AND day = ? AND status = 'pending'
                """,
                (confirmer_id, now, guild_id, day),
            )
            if claimed.rowcount != 1:
                connection.rollback()
                return False, "تم اعتماد هذا التقرير مسبقاً أو يجري اعتماده الآن.", []
            rows = connection.execute(
                """
                SELECT * FROM daily_points
                WHERE guild_id = ? AND day = ? AND status = 'pending'
                """,
                (guild_id, day),
            ).fetchall()
            user_ids: list[int] = []
            for row in rows:
                self._ensure_rows(connection, guild_id, row["user_id"], day)
                connection.execute(
                    """
                    UPDATE admin_points SET
                        total_points = total_points + ?,
                        ticket_points = ticket_points + ?,
                        event_points = event_points + ?,
                        message_points = message_points + ?,
                        image_points = image_points + ?,
                        video_points = video_points + ?
                    WHERE guild_id = ? AND user_id = ?
                    """,
                    (
                        row["total_points"],
                        row["ticket_points"],
                        row["event_points"],
                        row["message_points"],
                        row["image_points"],
                        row["video_points"],
                        guild_id,
                        row["user_id"],
                    ),
                )
                connection.execute(
                    """
                    UPDATE daily_points
                    SET status = 'approved', confirmed_by = ?, confirmed_at = ?
                    WHERE guild_id = ? AND user_id = ? AND day = ?
                      AND status = 'pending'
                    """,
                    (confirmer_id, now, guild_id, row["user_id"], day),
                )
                user_ids.append(row["user_id"])
            connection.execute(
                """
                UPDATE daily_reports SET status = 'confirmed'
                WHERE guild_id = ? AND day = ? AND status = 'confirming'
                """,
                (guild_id, day),
            )
            connection.commit()
            return True, "تم اعتماد نقاط اليوم بنجاح.", user_ids
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def admin(self, guild_id: int, user_id: int) -> sqlite3.Row | None:
        connection = self.connect()
        try:
            return connection.execute(
                "SELECT * FROM admin_points WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()
        finally:
            connection.close()


database = AdminPointsDatabase()
_db_lock = asyncio.Lock()


async def ensure_junior_role(member: discord.Member) -> bool:
    if not is_current_admin(member):
        return False
    role_ids = role_ids_for(member.guild.id)
    if any(
        role_ids.get(name)
        and any(role.id == role_ids[name] for role in member.roles)
        for name in MANUAL_RANKS
    ):
        return False
    junior_id = role_ids.get("Junior", 0)
    junior = member.guild.get_role(junior_id) if junior_id else None
    if junior is None or junior in member.roles:
        return False
    try:
        await member.add_roles(junior, reason="LEON admin points initial rank")
        return True
    except (discord.Forbidden, discord.HTTPException):
        return False


async def register_counter(
    member: discord.Member,
    source: str,
    object_id: int,
    channel_id: int | None = None,
) -> bool:
    if member.bot or not is_current_admin(member) or source not in COUNTER_CONFIG:
        return False
    if source in {"images", "videos"}:
        if member.guild.id != BT_GUILD_ID or not BT_GUILD_ID:
            return False
        allowed = BT_IMAGE_CHANNEL_IDS if source == "images" else BT_VIDEO_CHANNEL_IDS
        if channel_id not in allowed:
            return False
    activity_id = f"{source}:{member.guild.id}:{member.id}:{object_id}"
    async with _db_lock:
        return await asyncio.to_thread(
            database.count_activity,
            member.guild.id,
            member.id,
            current_day(),
            source,
            activity_id,
        )


async def register_ticket_points(
    guild: discord.Guild,
    staff_id: int,
    ticket_id: int,
    stars: int,
    day: str | None = None,
) -> bool:
    if stars not in range(1, 6) or not is_supported_guild(guild.id):
        return False
    member = guild.get_member(staff_id)
    if member is None:
        try:
            member = await guild.fetch_member(staff_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return False
    if member is None or not is_current_admin(member):
        return False
    points = 1 if stars <= 2 else 2 if stars <= 4 else 3
    async with _db_lock:
        return await asyncio.to_thread(
            database.add_points,
            guild.id,
            member.id,
            day or current_day(),
            points,
            "tickets",
            f"ticket:{guild.id}:{ticket_id}",
        )


def day_for_timestamp(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).astimezone(KUWAIT_TZ).strftime(
            "%Y-%m-%d"
        )
    except ValueError:
        return None


def _ticket_points_activity_exists(activity_id: str) -> bool:
    connection = database.connect()
    try:
        row = connection.execute(
            """
            SELECT 1 FROM counted_activities
            WHERE activity_id = ?
            """,
            (activity_id,),
        ).fetchone()
        return row is not None
    finally:
        connection.close()


async def ticket_points_activity_exists(
    guild_id: int,
    ticket_id: int,
) -> bool:
    """Check whether a ticket award was already committed."""
    return await asyncio.to_thread(
        _ticket_points_activity_exists,
        f"ticket:{guild_id}:{ticket_id}",
    )


async def register_event_points(
    member: discord.Member,
    game_id: int,
    participants: int,
    max_players: int | None = None,
) -> bool:
    if member.bot or not is_current_admin(member) or participants < 0:
        return False
    if max_players is not None and (max_players < 1 or participants > max_players):
        return False
    if max_players == 2:
        if participants != 2:
            return False
        points = 2
    else:
        if participants < 3:
            return False
        points = 3
    async with _db_lock:
        return await asyncio.to_thread(
            database.add_points,
            member.guild.id,
            member.id,
            current_day(),
            points,
            "events",
            f"game:{member.guild.id}:{game_id}",
        )


async def check_auto_promotion(member: discord.Member) -> bool:
    if not is_current_admin(member):
        return False
    role_ids = role_ids_for(member.guild.id)
    if any(
        role_ids.get(name)
        and any(role.id == role_ids[name] for role in member.roles)
        for name in MANUAL_RANKS
    ):
        return False
    row = await asyncio.to_thread(database.admin, member.guild.id, member.id)
    if row is None:
        return False
    target_name = rank_from_points(row["total_points"])
    target_id = role_ids.get(target_name, 0)
    target = member.guild.get_role(target_id) if target_id else None
    if target is None:
        return False
    point_roles = [
        role
        for name, _ in POINT_RANKS
        if (role_id := role_ids.get(name, 0))
        and (role := member.guild.get_role(role_id)) is not None
        and role in member.roles
    ]
    target_index = next(
        index for index, (name, _) in enumerate(POINT_RANKS) if name == target_name
    )
    owned_indexes = [
        index
        for index, (name, _) in enumerate(POINT_RANKS)
        if role_ids.get(name)
        and any(role.id == role_ids[name] for role in member.roles)
    ]
    if owned_indexes and max(owned_indexes) >= target_index:
        return False
    try:
        if target not in member.roles:
            await member.add_roles(target, reason="LEON approved admin points promotion")
        old_roles = [role for role in point_roles if role != target]
        if old_roles:
            await member.remove_roles(
                *old_roles,
                reason="LEON approved admin points promotion",
            )
        return True
    except (discord.Forbidden, discord.HTTPException):
        return False


async def send_daily_report(
    bot: commands.Bot,
    guild_id: int,
    day: str,
) -> bool:
    if not is_supported_guild(guild_id) or day >= current_day():
        return False
    guild = bot.get_guild(guild_id)
    channel_id = report_channel_id_for(guild_id)
    if guild is None or not channel_id:
        return False
    channel = guild.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel) or channel.guild.id != guild_id:
        return False
    async with _db_lock:
        report = await asyncio.to_thread(database.reserve_report, guild_id, day)
    if report["status"] == "confirmed" or report["message_id"]:
        return False
    marker = f"LEON-ADMIN-POINTS:{guild_id}:{day}"
    try:
        async for old_message in channel.history(limit=100):
            if old_message.author == bot.user and old_message.embeds:
                footer = old_message.embeds[0].footer.text or ""
                if footer == marker:
                    await asyncio.to_thread(
                        database.set_report_message,
                        guild_id,
                        day,
                        old_message.id,
                    )
                    return False
    except discord.HTTPException:
        pass
    rows = await asyncio.to_thread(database.daily_rows, guild_id, day)
    if not rows:
        return False
    lines = [
        f"<@{row['user_id']}> — **{row['total_points']} نقطة**"
        for row in rows
    ]
    chunks: list[str] = []
    current = ""
    for line in lines:
        if len(current) + len(line) + 1 > 3800:
            chunks.append(current)
            current = ""
        current += line + "\n"
    if current:
        chunks.append(current)
    if len(chunks) > 10:
        raise RuntimeError("Daily report exceeds Discord's ten-embed limit.")
    embeds = []
    for index, chunk in enumerate(chunks):
        embed = discord.Embed(
            title=(
‎                "📊 تقرير نقاط الإدارة اليومي"
                if index == 0
                else "📊 تقرير نقاط الإدارة اليومي — تابع"
            ),
            description=(
                (f"تقرير يوم **{day}**\n\n" if index == 0 else "") + chunk
            ),
            color=discord.Color.gold(),
            timestamp=datetime.now(KUWAIT_TZ),
        )
        embed.set_footer(text=marker)
        embeds.append(embed)
    message = await channel.send(embeds=embeds, view=DailyConfirmView())
    await asyncio.to_thread(
        database.set_report_message,
        guild_id,
        day,
        message.id,
    )
    return True


async def recover_pending_reports(bot: commands.Bot) -> None:
    for guild_id in supported_guild_ids():
        days = await asyncio.to_thread(database.pending_days, guild_id)
        for day in days:
            try:
                await send_daily_report(bot, guild_id, day)
            except (discord.HTTPException, sqlite3.Error) as exc:
                print(
                    f"[ADMIN POINTS REPORT ERROR] guild={guild_id} day={day}: {exc}",
                    flush=True,
                )


def validate_configuration(bot: commands.Bot) -> bool:
    """Validate that configured channels and roles belong to the intended guild."""
    valid = True
    for guild_id in supported_guild_ids():
        guild = bot.get_guild(guild_id)
        if guild is None:
            print(
                f"[ADMIN POINTS CONFIG ERROR] Guild {guild_id} is unavailable.",
                flush=True,
            )
            valid = False
            continue
        report_channel_id = report_channel_id_for(guild_id)
        channel = guild.get_channel(report_channel_id)
        if not isinstance(channel, discord.TextChannel):
            print(
                f"[ADMIN POINTS CONFIG ERROR] Report channel "
                f"{report_channel_id} is not a text channel in guild {guild_id}.",
                flush=True,
            )
            valid = False
        for rank_name, role_id in role_ids_for(guild_id).items():
            if guild.get_role(role_id) is None:
                print(
                    f"[ADMIN POINTS CONFIG ERROR] Role {rank_name} ({role_id}) "
                    f"is missing from guild {guild_id}.",
                    flush=True,
                )
                valid = False
    print(
        "[ADMIN POINTS] Configuration validation "
        + ("passed." if valid else "failed; invalid entries stay safely inactive."),
        flush=True,
    )
    return valid


class DailyConfirmView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="تأكيد النقاط",
        emoji="✅",
        style=discord.ButtonStyle.success,
        custom_id=REPORT_CUSTOM_ID,
    )
    async def confirm(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["DailyConfirmView"],
    ) -> None:
        if (
            interaction.guild is None
            or interaction.message is None
            or not isinstance(interaction.user, discord.Member)
            or not interaction.user.guild_permissions.administrator
        ):
            await interaction.response.send_message(
‎                "❌ تحتاج إلى صلاحية Administrator.",
                ephemeral=True,
            )
            return
        report = await asyncio.to_thread(
            database.report_for_message,
            interaction.guild.id,
            interaction.message.id,
        )
        if report is None:
            await interaction.response.send_message(
‎                "❌ لم يتم العثور على هذا التقرير.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        async with _db_lock:
            success, message, user_ids = await asyncio.to_thread(
                database.approve_day,
                interaction.guild.id,
                report["day"],
                interaction.user.id,
            )
        if success:
            for user_id in user_ids:
                member = interaction.guild.get_member(user_id)
                if member is not None:
                    await check_auto_promotion(member)
            button.disabled = True
            try:
                await interaction.message.edit(view=self)
            except discord.HTTPException:
                pass
        await interaction.followup.send(
            ("✅ " if success else "❌ ") + message,
            ephemeral=True,
        )


class AdminStatsView(discord.ui.View):
    def __init__(self, pages: list[discord.Embed], owner_id: int) -> None:
        super().__init__(timeout=120)
        self.pages = pages
        self.owner_id = owner_id
        self.index = 0

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
‎            "هذه الأزرار ليست خاصة بك.",
            ephemeral=True,
        )
        return False

    @discord.ui.button(label="السابق", style=discord.ButtonStyle.secondary)
    async def previous(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["AdminStatsView"],
    ) -> None:
        self.index = (self.index - 1) % len(self.pages)
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(label="التالي", style=discord.ButtonStyle.secondary)
    async def following(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["AdminStatsView"],
    ) -> None:
        self.index = (self.index + 1) % len(self.pages)
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)


class AdminPointsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.recovered = False
        self.daily_checker.start()

    async def cog_unload(self) -> None:
        self.daily_checker.cancel()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if not self.recovered:
            self.recovered = True
            validate_configuration(self.bot)
            await recover_pending_reports(self.bot)

    @commands.Cog.listener()
    async def on_member_update(
        self,
        before: discord.Member,
        after: discord.Member,
    ) -> None:
        if is_supported_guild(after.guild.id) and not is_current_admin(before):
            await ensure_junior_role(after)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if (
            message.guild is None
            or not is_supported_guild(message.guild.id)
            or not isinstance(message.author, discord.Member)
            or message.author.bot
            or not is_current_admin(message.author)
        ):
            return
        await ensure_junior_role(message.author)
        await register_counter(message.author, "messages", message.id)
        for attachment in message.attachments:
            content_type = (attachment.content_type or "").casefold()
            filename = attachment.filename.casefold()
            if content_type.startswith("image/") or filename.endswith(
                (".png", ".jpg", ".jpeg", ".gif", ".webp")
            ):
                await register_counter(
                    message.author,
                    "images",
                    attachment.id,
                    message.channel.id,
                )
            elif content_type.startswith("video/") or filename.endswith(
                (".mp4", ".mov", ".webm", ".mkv", ".avi")
            ):
                await register_counter(
                    message.author,
                    "videos",
                    attachment.id,
                    message.channel.id,
                )

    @tasks.loop(minutes=1)
    async def daily_checker(self) -> None:
        # Recovering is idempotent. Running it every minute avoids missing
        # midnight when the loop starts a few seconds after the minute boundary.
        await recover_pending_reports(self.bot)

    @daily_checker.before_loop
    async def before_daily_checker(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(name="adminstats", description="عرض نقاط الإداريين الحاليين")
    async def adminstats(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not is_supported_guild(interaction.guild.id):
            await interaction.response.send_message(
‎                "❌ هذا السيرفر غير مفعّل.",
                ephemeral=True,
            )
            return
        members = [member for member in interaction.guild.members if is_current_admin(member)]
        rows = []
        for member in members:
            row = await asyncio.to_thread(database.admin, interaction.guild.id, member.id)
            if row is not None:
                rows.append((member, row))
        rows.sort(key=lambda item: item[1]["total_points"], reverse=True)
        if not rows:
            await interaction.response.send_message("لا توجد بيانات إداريين حالياً.")
            return
        pages: list[discord.Embed] = []
        for start in range(0, len(rows), 10):
            page_rows = rows[start : start + 10]
            embed = discord.Embed(
                title="📊 نقاط الإداريين",
                description="\n".join(
                    f"**{start + index}.** {member.mention} — "
                    f"{current_rank(member) or 'غير محدد'} — "
                    f"**{row['total_points']} نقطة**"
                    for index, (member, row) in enumerate(page_rows, 1)
                ),
                color=discord.Color.gold(),
            )
            embed.set_footer(text=f"صفحة {len(pages) + 1}")
            pages.append(embed)
        view = AdminStatsView(pages, interaction.user.id) if len(pages) > 1 else None
        await interaction.response.send_message(embed=pages[0], view=view)

    @app_commands.command(name="admininfo", description="عرض تفاصيل نقاط إداري")
    @app_commands.describe(member="الإداري")
    async def admininfo(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
    ) -> None:
        if (
            interaction.guild is None
            or not is_supported_guild(interaction.guild.id)
            or not is_current_admin(member)
        ):
            await interaction.response.send_message(
‎                "❌ هذا العضو ليس إدارياً حالياً في سيرفر مفعّل.",
                ephemeral=True,
            )
            return
        row = await asyncio.to_thread(database.admin, interaction.guild.id, member.id)
        if row is None:
            await interaction.response.send_message(
‎                "❌ لا توجد بيانات لهذا الإداري.",
                ephemeral=True,
            )
            return
        embed = discord.Embed(
            title=f"📊 إحصائيات {member.display_name}",
            color=discord.Color.gold(),
        )
        embed.add_field(name="👤 الإداري", value=member.mention, inline=False)
        embed.add_field(
            name="🏅 الرتبة الحالية",
            value=current_rank(member) or "غير محدد",
        )
        embed.add_field(name="⭐ النقاط المعتمدة", value=str(row["total_points"]))
        upcoming = next_rank(row["total_points"])
        embed.add_field(
            name="📈 الترقية القادمة",
            value=(
                f"{upcoming[0]}\nالمطلوب: {upcoming[1]}\nالمتبقي: {upcoming[2]}"
                if upcoming
                else "Admin Staff آخر رتبة بالنقاط؛ ما بعدها يدوي."
            ),
            inline=False,
        )
        for label, column in (
‎            ("🎫 التذاكر", "ticket_points"),
‎            ("🎉 الفعاليات", "event_points"),
‎            ("💬 التفاعل", "message_points"),
        ):
            embed.add_field(name=label, value=f"{row[column]} نقطة")
        if interaction.guild.id == BT_GUILD_ID:
            embed.add_field(name="🖼️ الصور", value=f"{row['image_points']} نقطة")
            embed.add_field(name="🎬 المقاطع", value=f"{row['video_points']} نقطة")
        await interaction.response.send_message(embed=embed)


async def setup_admin_points(bot: commands.Bot) -> None:
    await asyncio.to_thread(database.initialize)
    bot.add_view(DailyConfirmView())
    await bot.add_cog(AdminPointsCog(bot))
    enabled = supported_guild_ids()
    print(
        "[ADMIN POINTS] Initialized"
        + (f" for guilds {enabled}." if enabled else " with IDs awaiting configuration."),
        flush=True,
    )
