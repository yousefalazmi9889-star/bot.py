"""LEON Discord bot with livestream, AI, moderation, and ticket features."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands, tasks
from flask import Flask
from google import genai

from .games import game_database, register_game_commands
from .community_games import setup as register_community_games
from .admin_points import (
    is_supported_guild as is_admin_points_guild,
    register_ticket_points,
    is_current_admin as is_admin_points_member,
    day_for_timestamp,
    recover_pending_reports,
    ticket_points_activity_exists,
    setup_admin_points,
)
from .security import GuildSecuritySettings, register_security
from .social_links import KICK_URL, TIKTOK_URL, YOUTUBE_URL

DEFAULT_CLEAR_AMOUNT = 10
KEEP_ALIVE_PORT = 8080
GEMINI_MODEL = "gemini-2.5-flash"
INACTIVITY_HOURS = 24
MAX_CLEAR_AMOUNT = 100
DATABASE_FILE = Path(__file__).resolve().parents[2] / "leon_tickets.db"
CATALOG_GUILD_ID = 1488301471371104256
CATALOG_LOG_CHANNEL_ID = 1488303245565628646
REMINDER_COLUMNS = frozenset({"member_reminded", "staff_reminded"})
LEGACY_COMPONENT_IDS = {
    "leon_ticket:claim": "claim_ticket_btn",
    "leon_ticket:unclaim": "legacy_ticket:unclaim",
    "leon_ticket:remind_member": "remind_member_btn",
    "leon_ticket:remind_staff": "remind_staff_btn",
    "leon_ticket:add_member": "legacy_ticket:add_member",
    "leon_ticket:remove_member": "legacy_ticket:remove_member",
    "leon_ticket:transcript": "legacy_ticket:transcript",
    "leon_ticket:close": "close_ticket_btn",
}
LEGACY_SELECT_ID = "support_select_menu"

STREAM_KEYWORDS = (
    "متى البث",
    "وين البث",
    "وين البث؟",
    "رابط البث",
    "رابط الكيك",
    "رابط القناة",
    "قناة كيك",
    "الكيك",
    "kick",
    "البث وين",
    "البث",
    "متى يفتح البث",
)

TICKET_TYPES = {
    "technical": ("دعم فني", "للمشاكل التقنية"),
    "general": ("استفسار عام", "للاستفسارات العامة"),
    "complaint": ("شكوى", "لتقديم شكوى"),
    "other": ("طلب آخر", "لأي طلب آخر"),
    "admin_application": ("👮 تقديم إدارة", "للتقديم على إدارة السيرفر"),
    "partnership": ("🤝 شراكة", "لطلب شراكة مع السيرفر"),
}
SUBMISSION_TYPES = frozenset({"admin_application", "partnership"})

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

web_app = Flask("leon_keep_alive")


@web_app.get("/")
def keep_alive_home() -> str:
    return "LEON Bot is Online!"


def keep_alive() -> threading.Thread:
    """Run the public health endpoint without blocking Discord."""
    thread = threading.Thread(
        target=lambda: web_app.run(
            host="0.0.0.0",
            port=KEEP_ALIVE_PORT,
            debug=False,
            use_reloader=False,
        ),
        name="leon-keep-alive",
        daemon=True,
    )
    thread.start()
    dev_domain = os.environ.get("REPLIT_DEV_DOMAIN")
    if dev_domain:
        print(
            f"[KEEP-ALIVE] Webview URL: https://{dev_domain}:{KEEP_ALIVE_PORT}/",
            flush=True,
        )
    else:
        print(
            f"[KEEP-ALIVE] Listening on 0.0.0.0:{KEEP_ALIVE_PORT}; "
            "the external Webview URL is provided by Replit.",
            flush=True,
        )
    return thread


@dataclass(frozen=True)
class Settings:
    """Ticket settings stored independently for one Discord guild."""

    guild_id: int
    ticket_category_id: int
    ticket_log_channel_id: int
    support_role_id: int


def utc_now() -> datetime:
    """Return the current timezone-aware UTC time."""
    return datetime.now(timezone.utc)


def utc_string(value: datetime | None = None) -> str:
    """Serialize a datetime for SQLite."""
    return (value or utc_now()).astimezone(timezone.utc).isoformat()


def parse_datetime(value: str | None) -> datetime | None:
    """Parse an ISO timestamp stored in SQLite."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class TicketDatabase:
    """Small synchronized SQLite repository for tickets and ratings."""

    def __init__(self, path: Path) -> None:
        self.connection = sqlite3.connect(
            path,
            check_same_thread=False,
        )
        self.connection.row_factory = sqlite3.Row
        self.lock = threading.RLock()

    def close(self) -> None:
        """Close the database connection during process shutdown."""
        with self.lock:
            self.connection.close()

    def initialize(self) -> None:
        """Create tables and add columns needed by older database files."""
        with self.lock:
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tickets (
                    channel_id INTEGER PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    owner_id INTEGER NOT NULL,
                    staff_id INTEGER NOT NULL DEFAULT 0,
                    ticket_type TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_member_message TEXT,
                    closed INTEGER NOT NULL DEFAULT 0,
                    closed_at TEXT,
                    close_reason TEXT,
                    closed_by TEXT,
                    member_reminded INTEGER NOT NULL DEFAULT 0,
                    staff_reminded INTEGER NOT NULL DEFAULT 0,
                    member_reminded_at TEXT,
                    staff_reminded_at TEXT,
                    transcript_created_at TEXT,
                    transcript_message_id INTEGER,
                    auto_closed INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS ticket_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    actor_id INTEGER,
                    details TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS open_ticket_reservations (
                    guild_id INTEGER NOT NULL,
                    owner_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (guild_id, owner_id)
                )
                """
            )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS guild_ticket_settings (
                    guild_id INTEGER PRIMARY KEY,
                    ticket_category_id INTEGER,
                    ticket_log_channel_id INTEGER NOT NULL DEFAULT 0,
                    support_role_id INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS guild_security_settings (
                    guild_id INTEGER PRIMARY KEY,
                    security_log_channel_id INTEGER NOT NULL,
                    punish_other_bots INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self.connection.execute(
                "DELETE FROM open_ticket_reservations WHERE channel_id = 0"
            )
            self.connection.execute(
                """
                INSERT OR IGNORE INTO open_ticket_reservations (
                    guild_id, owner_id, channel_id, created_at
                )
                SELECT guild_id, owner_id, channel_id, created_at
                FROM tickets WHERE closed = 0
                """
            )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS ratings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_channel_id INTEGER NOT NULL UNIQUE,
                    owner_id INTEGER NOT NULL,
                    staff_id INTEGER NOT NULL,
                    rating INTEGER,
                    created_at TEXT NOT NULL,
                    rated_at TEXT,
                    admin_points_processed INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS ticket_submissions (
                    channel_id INTEGER PRIMARY KEY,
                    submission_type TEXT NOT NULL,
                    responses_json TEXT NOT NULL,
                    decision TEXT,
                    decision_by INTEGER,
                    decision_at TEXT
                )
                """
            )

            existing_columns = {
                row["name"]
                for row in self.connection.execute("PRAGMA table_info(tickets)")
            }
            migrations = {
                "close_reason": "TEXT",
                "closed_by": "TEXT",
                "member_reminded": "INTEGER NOT NULL DEFAULT 0",
                "staff_reminded": "INTEGER NOT NULL DEFAULT 0",
                "member_reminded_at": "TEXT",
                "staff_reminded_at": "TEXT",
                "transcript_created_at": "TEXT",
                "transcript_message_id": "INTEGER",
                "auto_closed": "INTEGER NOT NULL DEFAULT 0",
            }
            for column, column_type in migrations.items():
                if column not in existing_columns:
                    self.connection.execute(
                        f"ALTER TABLE tickets ADD COLUMN {column} {column_type}"
                    )
            rating_columns = {
                row["name"]
                for row in self.connection.execute("PRAGMA table_info(ratings)")
            }
            if "admin_points_processed" not in rating_columns:
                self.connection.execute(
                    """
                    ALTER TABLE ratings
                    ADD COLUMN admin_points_processed INTEGER NOT NULL DEFAULT 0
                    """
                )
            self._seed_catalog_settings()
            self._seed_catalog_security_settings()
            self.connection.commit()

    def _seed_catalog_settings(self) -> None:
        """Ensure the explicitly configured Catalog guild has its own row."""
        self.connection.execute(
            """
            INSERT OR IGNORE INTO guild_ticket_settings (
                guild_id, ticket_category_id, ticket_log_channel_id,
                support_role_id, updated_at
            )
            VALUES (?, NULL, ?, 0, ?)
            """,
            (CATALOG_GUILD_ID, CATALOG_LOG_CHANNEL_ID, utc_string()),
        )

    def _seed_catalog_security_settings(self) -> None:
        """Ensure Catalog has an isolated security log destination."""
        self.connection.execute(
            """
            INSERT OR IGNORE INTO guild_security_settings (
                guild_id, security_log_channel_id,
                punish_other_bots, updated_at
            )
            VALUES (?, ?, 0, ?)
            """,
            (CATALOG_GUILD_ID, 1544577415668437024, utc_string()),
        )

    def get_security_settings(
        self,
        guild_id: int,
    ) -> GuildSecuritySettings | None:
        with self.lock:
            row = self.connection.execute(
                """
                SELECT security_log_channel_id, punish_other_bots
                FROM guild_security_settings
                WHERE guild_id = ?
                """,
                (guild_id,),
            ).fetchone()
            if row is None:
                return None
            return GuildSecuritySettings(
                log_channel_id=row["security_log_channel_id"],
                punish_other_bots=bool(row["punish_other_bots"]),
            )

    def save_security_settings(
        self,
        guild_id: int,
        log_channel_id: int,
        punish_other_bots: bool = False,
    ) -> None:
        with self.lock:
            self.connection.execute(
                """
                INSERT INTO guild_security_settings (
                    guild_id, security_log_channel_id,
                    punish_other_bots, updated_at
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    security_log_channel_id =
                        excluded.security_log_channel_id,
                    punish_other_bots = excluded.punish_other_bots,
                    updated_at = excluded.updated_at
                """,
                (
                    guild_id,
                    log_channel_id,
                    int(punish_other_bots),
                    utc_string(),
                ),
            )
            self.connection.commit()

    def get_ticket_settings(self, guild_id: int) -> Settings | None:
        with self.lock:
            row = self.connection.execute(
                """
                SELECT guild_id, ticket_category_id, ticket_log_channel_id,
                       support_role_id
                FROM guild_ticket_settings WHERE guild_id = ?
                """,
                (guild_id,),
            ).fetchone()
            if row is None:
                return None
            return Settings(
                guild_id=row["guild_id"],
                ticket_category_id=row["ticket_category_id"] or 0,
                ticket_log_channel_id=row["ticket_log_channel_id"],
                support_role_id=row["support_role_id"],
            )

    def save_ticket_settings(
        self,
        guild_id: int,
        category_id: int | None,
        log_channel_id: int,
        support_role_id: int = 0,
    ) -> None:
        with self.lock:
            self.connection.execute(
                """
                INSERT INTO guild_ticket_settings (
                    guild_id, ticket_category_id, ticket_log_channel_id,
                    support_role_id, updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    ticket_category_id = excluded.ticket_category_id,
                    ticket_log_channel_id = excluded.ticket_log_channel_id,
                    support_role_id = excluded.support_role_id,
                    updated_at = excluded.updated_at
                """,
                (
                    guild_id,
                    category_id,
                    log_channel_id,
                    support_role_id,
                    utc_string(),
                ),
            )
            self.connection.commit()

    def create_ticket(
        self,
        channel_id: int,
        guild_id: int,
        owner_id: int,
        ticket_type: str,
        responses: dict[str, str] | None = None,
    ) -> None:
        with self.lock:
            try:
                self.connection.execute(
                    """
                    INSERT INTO tickets (
                        channel_id, guild_id, owner_id, staff_id, ticket_type,
                        created_at, last_member_message, closed, closed_at,
                        close_reason, closed_by, member_reminded, staff_reminded,
                        member_reminded_at, staff_reminded_at,
                        transcript_created_at, transcript_message_id, auto_closed
                    )
                    VALUES (?, ?, ?, 0, ?, ?, NULL, 0, NULL, NULL, NULL, 0, 0,
                            NULL, NULL, NULL, NULL, 0)
                    """,
                    (
                        channel_id,
                        guild_id,
                        owner_id,
                        ticket_type,
                        utc_string(),
                    ),
                )
                if responses is not None:
                    self.connection.execute(
                        """
                        INSERT INTO ticket_submissions (
                            channel_id, submission_type, responses_json,
                            decision, decision_by, decision_at
                        )
                        VALUES (?, ?, ?, NULL, NULL, NULL)
                        """,
                        (
                            channel_id,
                            ticket_type,
                            json.dumps(responses, ensure_ascii=False),
                        ),
                    )
                self.connection.commit()
            except sqlite3.Error:
                self.connection.rollback()
                raise

    def get_submission(self, channel_id: int) -> sqlite3.Row | None:
        with self.lock:
            return self.connection.execute(
                """
                SELECT * FROM ticket_submissions
                WHERE channel_id = ?
                """,
                (channel_id,),
            ).fetchone()

    def decide_submission(
        self,
        channel_id: int,
        decision: str,
        decision_by: int,
    ) -> bool:
        """Atomically accept or reject a submission exactly once."""
        if decision not in {"accepted", "rejected"}:
            raise ValueError(f"Unknown submission decision: {decision}")
        with self.lock:
            cursor = self.connection.execute(
                """
                UPDATE ticket_submissions
                SET decision = ?, decision_by = ?, decision_at = ?
                WHERE channel_id = ? AND decision IS NULL
                """,
                (decision, decision_by, utc_string(), channel_id),
            )
            self.connection.commit()
            return cursor.rowcount == 1

    def reserve_open_ticket(self, guild_id: int, owner_id: int) -> bool:
        with self.lock:
            try:
                self.connection.execute(
                    """
                    INSERT INTO open_ticket_reservations (
                        guild_id, owner_id, channel_id, created_at
                    )
                    VALUES (?, ?, 0, ?)
                    """,
                    (guild_id, owner_id, utc_string()),
                )
                self.connection.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def finalize_open_ticket(
        self,
        guild_id: int,
        owner_id: int,
        channel_id: int,
    ) -> None:
        with self.lock:
            self.connection.execute(
                """
                UPDATE open_ticket_reservations SET channel_id = ?
                WHERE guild_id = ? AND owner_id = ?
                """,
                (channel_id, guild_id, owner_id),
            )
            self.connection.commit()

    def release_open_ticket(self, guild_id: int, owner_id: int) -> None:
        with self.lock:
            self.connection.execute(
                """
                DELETE FROM open_ticket_reservations
                WHERE guild_id = ? AND owner_id = ?
                """,
                (guild_id, owner_id),
            )
            self.connection.commit()

    def get_ticket(self, channel_id: int) -> sqlite3.Row | None:
        with self.lock:
            return self.connection.execute(
                "SELECT * FROM tickets WHERE channel_id = ?",
                (channel_id,),
            ).fetchone()

    def get_open_ticket_for_owner(
        self,
        guild_id: int,
        owner_id: int,
    ) -> sqlite3.Row | None:
        with self.lock:
            return self.connection.execute(
                """
                SELECT * FROM tickets
                WHERE guild_id = ? AND owner_id = ? AND closed = 0
                LIMIT 1
                """,
                (guild_id, owner_id),
            ).fetchone()

    def open_tickets(self) -> list[sqlite3.Row]:
        with self.lock:
            return list(
                self.connection.execute(
                    "SELECT * FROM tickets WHERE closed = 0"
                ).fetchall()
            )

    def update_last_member_message(
        self,
        channel_id: int,
        created_at: datetime,
    ) -> None:
        with self.lock:
            self.connection.execute(
                """
                UPDATE tickets
                SET last_member_message = ?
                WHERE channel_id = ? AND closed = 0
                """,
                (utc_string(created_at), channel_id),
            )
            self.connection.commit()

    def log_event(
        self,
        channel_id: int,
        event_type: str,
        actor_id: int | None = None,
        details: str | None = None,
    ) -> None:
        with self.lock:
            self.connection.execute(
                """
                INSERT INTO ticket_events (
                    channel_id, event_type, actor_id, details, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (channel_id, event_type, actor_id, details, utc_string()),
            )
            self.connection.commit()

    def claim_ticket(self, channel_id: int, staff_id: int) -> bool:
        """Atomically assign the first staff member who claims a ticket."""
        with self.lock:
            cursor = self.connection.execute(
                """
                UPDATE tickets
                SET staff_id = ?
                WHERE channel_id = ? AND staff_id = 0 AND closed = 0
                """,
                (staff_id, channel_id),
            )
            self.connection.commit()
            return cursor.rowcount == 1

    def unclaim_ticket(self, channel_id: int, staff_id: int) -> bool:
        """Only the current claimant can release their claim."""
        with self.lock:
            cursor = self.connection.execute(
                """
                UPDATE tickets
                SET staff_id = 0
                WHERE channel_id = ? AND staff_id = ? AND closed = 0
                """,
                (channel_id, staff_id),
            )
            self.connection.commit()
            return cursor.rowcount == 1

    def claim_reminder(self, channel_id: int, reminder_column: str) -> bool:
        """Reserve a reminder atomically; 0=unused, 1=sent, 2=in progress."""
        if reminder_column not in REMINDER_COLUMNS:
            raise ValueError(f"Unknown reminder column: {reminder_column}")
        with self.lock:
            cursor = self.connection.execute(
                f"""
                UPDATE tickets
                SET {reminder_column} = 2
                WHERE channel_id = ? AND closed = 0 AND {reminder_column} = 0
                """,
                (channel_id,),
            )
            self.connection.commit()
            return cursor.rowcount == 1

    def complete_reminder(self, channel_id: int, reminder_column: str) -> None:
        """Mark a successfully delivered reminder as permanently used."""
        if reminder_column not in REMINDER_COLUMNS:
            raise ValueError(f"Unknown reminder column: {reminder_column}")
        with self.lock:
            self.connection.execute(
                f"""
                UPDATE tickets
                SET {reminder_column} = 1
                WHERE channel_id = ? AND {reminder_column} = 2
                """,
                (channel_id,),
            )
            self.connection.commit()

    def release_reminder(self, channel_id: int, reminder_column: str) -> None:
        """Return a failed reminder reservation to the unused state."""
        if reminder_column not in REMINDER_COLUMNS:
            raise ValueError(f"Unknown reminder column: {reminder_column}")
        with self.lock:
            self.connection.execute(
                f"""
                UPDATE tickets
                SET {reminder_column} = 0
                WHERE channel_id = ? AND {reminder_column} = 2
                """,
                (channel_id,),
            )
            self.connection.commit()

    def close_ticket(
        self,
        channel_id: int,
        reason: str,
        closed_by: str,
        auto_closed: bool = False,
    ) -> sqlite3.Row | None:
        """Atomically mark a ticket closed and return its previous state."""
        with self.lock:
            row = self.connection.execute(
                "SELECT * FROM tickets WHERE channel_id = ?",
                (channel_id,),
            ).fetchone()
            if row is None or row["closed"]:
                return None

            self.connection.execute(
                """
                UPDATE tickets
                SET closed = 1, closed_at = ?, close_reason = ?,
                    closed_by = ?, auto_closed = ?
                WHERE channel_id = ? AND closed = 0
                """,
                (utc_string(), reason, closed_by, int(auto_closed), channel_id),
            )
            self.connection.execute(
                """
                DELETE FROM open_ticket_reservations
                WHERE guild_id = ? AND owner_id = ?
                """,
                (row["guild_id"], row["owner_id"]),
            )
            self.connection.commit()
            return row

    def mark_reminder_sent(
        self,
        channel_id: int,
        reminder_column: str,
        timestamp_column: str,
    ) -> None:
        if reminder_column not in REMINDER_COLUMNS:
            raise ValueError(f"Unknown reminder column: {reminder_column}")
        if timestamp_column not in {
            "member_reminded_at",
            "staff_reminded_at",
        }:
            raise ValueError(f"Unknown reminder timestamp: {timestamp_column}")
        with self.lock:
            self.connection.execute(
                f"""
                UPDATE tickets
                SET {reminder_column} = 1, {timestamp_column} = ?
                WHERE channel_id = ? AND {reminder_column} = 2
                """,
                (utc_string(), channel_id),
            )
            self.connection.commit()

    def mark_transcript(
        self,
        channel_id: int,
        message_id: int | None = None,
    ) -> None:
        with self.lock:
            self.connection.execute(
                """
                UPDATE tickets
                SET transcript_created_at = ?, transcript_message_id = ?
                WHERE channel_id = ?
                """,
                (utc_string(), message_id, channel_id),
            )
            self.connection.commit()

    def create_rating(
        self,
        ticket_channel_id: int,
        owner_id: int,
        staff_id: int,
    ) -> sqlite3.Row:
        with self.lock:
            self.connection.execute(
                """
                INSERT OR IGNORE INTO ratings (
                    ticket_channel_id, owner_id, staff_id, rating,
                    created_at, rated_at
                )
                VALUES (?, ?, ?, NULL, ?, NULL)
                """,
                (ticket_channel_id, owner_id, staff_id, utc_string()),
            )
            self.connection.commit()
            row = self.connection.execute(
                "SELECT * FROM ratings WHERE ticket_channel_id = ?",
                (ticket_channel_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("Could not create the ticket rating record.")
            return row

    def get_rating(self, rating_id: int) -> sqlite3.Row | None:
        with self.lock:
            return self.connection.execute(
                "SELECT * FROM ratings WHERE id = ?",
                (rating_id,),
            ).fetchone()

    def pending_ratings(self) -> list[sqlite3.Row]:
        with self.lock:
            return list(
                self.connection.execute(
                    "SELECT * FROM ratings WHERE rating IS NULL"
                ).fetchall()
            )

    def unprocessed_rating_points(self) -> list[sqlite3.Row]:
        """Return submitted ratings whose admin-point outcome is not recorded."""
        with self.lock:
            return list(
                self.connection.execute(
                    """
                    SELECT ratings.*, tickets.guild_id, tickets.closed,
                           tickets.staff_id AS ticket_staff_id
                    FROM ratings
                    JOIN tickets
                      ON tickets.channel_id = ratings.ticket_channel_id
                    WHERE ratings.rating IS NOT NULL
                      AND ratings.admin_points_processed = 0
                    """
                ).fetchall()
            )

    def rated_ratings(self) -> list[sqlite3.Row]:
        with self.lock:
            return list(
                self.connection.execute(
                    """
                    SELECT ratings.*, tickets.guild_id, tickets.closed,
                           tickets.staff_id AS ticket_staff_id
                    FROM ratings
                    JOIN tickets
                      ON tickets.channel_id = ratings.ticket_channel_id
                    WHERE ratings.rating IS NOT NULL
                    """
                ).fetchall()
            )

    def reset_rating_points_processed(self, rating_id: int) -> None:
        with self.lock:
            self.connection.execute(
                """
                UPDATE ratings SET admin_points_processed = 0
                WHERE id = ? AND rating IS NOT NULL
                """,
                (rating_id,),
            )
            self.connection.commit()

    def mark_rating_points_processed(self, rating_id: int) -> None:
        with self.lock:
            self.connection.execute(
                """
                UPDATE ratings SET admin_points_processed = 1
                WHERE id = ? AND rating IS NOT NULL
                """,
                (rating_id,),
            )
            self.connection.commit()

    def submit_rating(
        self,
        rating_id: int,
        owner_id: int,
        rating: int,
    ) -> bool:
        with self.lock:
            cursor = self.connection.execute(
                """
                UPDATE ratings
                SET rating = ?, rated_at = ?
                WHERE id = ? AND owner_id = ? AND rating IS NULL
                """,
                (rating, utc_string(), rating_id, owner_id),
            )
            self.connection.commit()
            return cursor.rowcount == 1

    def staff_statistics(self, staff_id: int) -> dict[str, Any]:
        with self.lock:
            closed = self.connection.execute(
                """
                SELECT COUNT(*) AS total FROM tickets
                WHERE staff_id = ? AND closed = 1
                """,
                (staff_id,),
            ).fetchone()["total"]
            rating_data = self.connection.execute(
                """
                SELECT COUNT(*) AS count,
                       COALESCE(SUM(rating), 0) AS total,
                       COALESCE(AVG(rating), 0) AS average
                FROM ratings
                WHERE staff_id = ? AND rating IS NOT NULL
                """,
                (staff_id,),
            ).fetchone()
            stars = {
                star: self.connection.execute(
                    """
                    SELECT COUNT(*) AS total FROM ratings
                    WHERE staff_id = ? AND rating = ?
                    """,
                    (staff_id, star),
                ).fetchone()["total"]
                for star in range(1, 6)
            }
            return {
                "closed": closed,
                "ratings_count": rating_data["count"],
                "total_rating": rating_data["total"],
                "average": float(rating_data["average"]),
                "stars": stars,
            }

    def all_staff_statistics(self) -> list[dict[str, Any]]:
        """Return closed-ticket and rating statistics for every staff member."""
        with self.lock:
            staff_ids = {
                row["staff_id"]
                for row in self.connection.execute(
                    """
                    SELECT staff_id FROM tickets
                    WHERE staff_id > 0
                    UNION
                    SELECT staff_id FROM ratings
                    WHERE staff_id > 0
                    """
                )
            }
            result: list[dict[str, Any]] = []
            for staff_id in staff_ids:
                row = self.connection.execute(
                    """
                    SELECT
                        (SELECT COUNT(*) FROM tickets
                         WHERE staff_id = ? AND closed = 1) AS closed,
                        COUNT(r.rating) AS ratings_count,
                        COALESCE(SUM(r.rating), 0) AS total_rating,
                        COALESCE(AVG(r.rating), 0) AS average
                    FROM ratings r
                    WHERE r.staff_id = ? AND r.rating IS NOT NULL
                    """,
                    (staff_id, staff_id),
                ).fetchone()
                stars = {
                    star: self.connection.execute(
                        """
                        SELECT COUNT(*) AS total FROM ratings
                        WHERE staff_id = ? AND rating = ?
                        """,
                        (staff_id, star),
                    ).fetchone()["total"]
                    for star in range(1, 6)
                }
                result.append(
                    {
                        "staff_id": staff_id,
                        "closed": row["closed"],
                        "ratings_count": row["ratings_count"],
                        "total_rating": row["total_rating"],
                        "average": float(row["average"]),
                        "stars": stars,
                    }
                )
            return sorted(
                result,
                key=lambda value: (
                    value["average"],
                    value["ratings_count"],
                ),
                reverse=True,
            )


database = TicketDatabase(DATABASE_FILE)
rating_points_recovered = False


async def process_rating_points(record: sqlite3.Row) -> None:
    """Process one durable ticket-rating points outbox entry."""
    if (
        not record["closed"]
        or record["ticket_staff_id"] != record["staff_id"]
        or not is_admin_points_guild(record["guild_id"])
    ):
        if is_admin_points_guild(record["guild_id"]):
            database.mark_rating_points_processed(record["id"])
        return
    guild = bot.get_guild(record["guild_id"])
    if guild is None:
        return
    rating_day = day_for_timestamp(record["rated_at"])
    awarded = await register_ticket_points(
        guild,
        record["staff_id"],
        record["ticket_channel_id"],
        record["rating"],
        day=rating_day,
    )
    if awarded or await ticket_points_activity_exists(
        record["guild_id"],
        record["ticket_channel_id"],
    ):
        database.mark_rating_points_processed(record["id"])


async def recover_missing_rating_points() -> None:
    """Reopen only awards that were falsely marked processed without an activity."""
    for record in database.rated_ratings():
        if (
            record["admin_points_processed"] == 0
            or not record["closed"]
            or record["ticket_staff_id"] != record["staff_id"]
            or not is_admin_points_guild(record["guild_id"])
        ):
            continue
        guild = bot.get_guild(record["guild_id"])
        if guild is None:
            continue
        member = guild.get_member(record["staff_id"])
        if member is None:
            try:
                member = await guild.fetch_member(record["staff_id"])
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                continue
        if (
            is_admin_points_member(member)
            and not await ticket_points_activity_exists(
                record["guild_id"],
                record["ticket_channel_id"],
            )
        ):
            database.reset_rating_points_processed(record["id"])


async def recover_rating_points() -> None:
    await recover_missing_rating_points()
    for record in database.unprocessed_rating_points():
        try:
            await process_rating_points(record)
        except (sqlite3.Error, discord.HTTPException) as exc:
            print(
                f"[ADMIN TICKET POINTS RECOVERY ERROR] "
                f"rating={record['id']} {type(exc).__name__}: {exc}",
                flush=True,
            )


class LeonBot(commands.Bot):
    """Bot subclass that restores persistent views after every process start."""

    guild_commands_synced = False

    async def setup_hook(self) -> None:
        database.initialize()
        game_database.initialize()
        await setup_admin_points(self)
        await register_security(
            self,
            settings_provider=database.get_security_settings,
            ticket_channel_check=lambda channel_id: (
                database.get_ticket(channel_id) is not None
            ),
        )
        await register_community_games(self)
        print(
            "[COGS] Registered security and community game systems.",
            flush=True,
        )
        print(f"[DATABASE] Using {DATABASE_FILE}", flush=True)
        self.add_view(TicketLauncher())
        self.add_view(TicketLauncher(legacy=True))
        self.add_view(TicketControlView())
        self.add_view(TicketControlView(legacy=True))
        self.add_view(SubmissionDecisionView())
        print(
            "[VIEWS] Registered current and legacy ticket views.",
            flush=True,
        )
        for rating in database.pending_ratings():
            self.add_view(
                RatingView(
                    rating_id=rating["id"],
                    staff_id=rating["staff_id"],
                )
            )

        try:
            synced = await self.tree.sync()
            command_names = ", ".join(
                sorted(command.name for command in synced)
            )
            print(
                f"[READY] Synced {len(synced)} slash command(s): "
                f"{command_names}.",
                flush=True,
            )
        except discord.HTTPException as exc:
            print(f"[SYNC ERROR] {exc}", flush=True)

        if not inactivity_checker.is_running():
            inactivity_checker.start()

    async def close(self) -> None:
        if inactivity_checker.is_running():
            inactivity_checker.cancel()
        await super().close()
        database.close()
        game_database.close()


bot = LeonBot(
    command_prefix="!",
    case_insensitive=True,
    intents=intents,
)
register_game_commands(bot)


def is_support_staff(member: discord.abc.User) -> bool:
    """Check whether a member can manage tickets."""
    if not isinstance(member, discord.Member):
        return False
    return member.guild_permissions.manage_channels


def parse_clear_amount(content: str) -> int | None:
    """Parse an unprefixed Arabic clear request."""
    parts = content.strip().split()
    if not parts or parts[0] != "مسح":
        return None
    if len(parts) == 1:
        return DEFAULT_CLEAR_AMOUNT
    if len(parts) != 2:
        return None
    try:
        amount = int(parts[1])
    except ValueError:
        return None
    return amount if 1 <= amount <= MAX_CLEAR_AMOUNT else None


def is_unprefixed_clear_request(content: str) -> bool:
    parts = content.strip().split()
    return bool(parts) and parts[0] == "مسح"


async def send_clear_error(message: discord.Message) -> None:
    await message.channel.send(
        f"الاستخدام الصحيح: `مسح [العدد]` أو `!مسح [العدد]` "
        f"(من 1 إلى {MAX_CLEAR_AMOUNT}). العدد الافتراضي هو "
        f"{DEFAULT_CLEAR_AMOUNT}.",
        delete_after=8,
    )


async def clear_messages(message: discord.Message, amount: int) -> None:
    """Bulk-delete messages after checking both sides' permissions."""
    if not isinstance(message.channel, discord.TextChannel):
        return
    if not isinstance(message.author, discord.Member):
        return
    if not message.author.guild_permissions.manage_messages:
        await message.channel.send("ليس لديك صلاحية حذف الرسائل.", delete_after=8)
        return

    bot_member = message.guild.me
    if bot_member is None or not message.channel.permissions_for(
        bot_member
    ).manage_messages:
        await message.channel.send(
            "لا أملك صلاحية إدارة الرسائل في هذه القناة.",
            delete_after=8,
        )
        return

    try:
        deleted = await message.channel.purge(limit=amount + 1)
    except discord.Forbidden:
        await message.channel.send(
            "تعذر حذف الرسائل. تأكد من صلاحيات البوت.",
            delete_after=8,
        )
        return

    await message.channel.send(
        f"تم حذف {max(len(deleted) - 1, 0)} رسالة.",
        delete_after=5,
    )


@bot.command(name="live")
async def live_command(ctx: commands.Context[LeonBot]) -> None:
    embed = discord.Embed(
        title="بث استريمر LEON!",
        description=(
            "حياكم الله جميعاً في البث المباشر على منصة **Kick**!\n\n"
            f"اضغط على الرابط للمتابعة والانضمام:\n{KICK_URL}"
        ),
        color=discord.Color.green(),
    )
    if bot.user is not None:
        embed.set_thumbnail(url=bot.user.display_avatar.url)
    embed.set_footer(text="نتمنى لكم مشاهدة ممتعة!")
    await ctx.send(embed=embed)


@bot.command(name="روابط", aliases=("مواقع", "social"))
async def social_command(ctx: commands.Context[LeonBot]) -> None:
    embed = discord.Embed(
        title="روابط LEON الرسمية",
        description="تابع LEON على منصاته الرسمية:",
        color=discord.Color.blue(),
    )
    embed.add_field(
        name="🎥 YouTube",
        value=YOUTUBE_URL,
        inline=False,
    )
    embed.add_field(
        name="🟢 Kick",
        value=KICK_URL,
        inline=False,
    )
    embed.add_field(
        name="🎵 TikTok",
        value=TIKTOK_URL,
        inline=False,
    )
    await ctx.send(embed=embed)


@bot.command(name="مسح")
async def clear_command(
    ctx: commands.Context[LeonBot],
    amount: int = DEFAULT_CLEAR_AMOUNT,
) -> None:
    if not 1 <= amount <= MAX_CLEAR_AMOUNT:
        await send_clear_error(ctx.message)
        return
    await clear_messages(ctx.message, amount)


def member_has_ai_role(member: discord.abc.User) -> bool:
    """Return true only for a member with a role named exactly `AI`."""
    return any(
        getattr(role, "name", None) == "AI"
        for role in getattr(member, "roles", ())
    )


def remove_bot_mention(content: str, bot_user: discord.ClientUser) -> str:
    return re.sub(rf"<@!?{bot_user.id}>", "", content).strip()


def generate_gemini_response(prompt: str) -> str:
    """Call Gemini with the user-provided API key."""
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config={
            "system_instruction": (
                "You are LEON's helpful Discord community assistant. "
                "Reply concisely in the same language as the user."
            )
        },
    )
    if not response.text:
        raise RuntimeError("Gemini returned an empty response.")
    return response.text.strip()


async def send_gemini_response(
    message: discord.Message,
    prompt: str,
) -> None:
    if not prompt:
        await message.reply("اكتب سؤالك بعد منشن البوت.")
        return
    try:
        response = await asyncio.to_thread(generate_gemini_response, prompt)
    except Exception as exc:
        print(f"[GEMINI ERROR] {type(exc).__name__}: {exc}", flush=True)
        await message.reply("تعذر الحصول على رد من الذكاء الاصطناعي حالياً.")
        return

    for start in range(0, len(response), 2000):
        await message.reply(response[start : start + 2000])


class RatingButton(discord.ui.Button["RatingView"]):
    """Persistent one-to-five-star rating button."""

    def __init__(self, rating: int, rating_id: int, staff_id: int) -> None:
        self.rating = rating
        self.rating_id = rating_id
        self.staff_id = staff_id
        super().__init__(
            label="⭐" * rating,
            style=discord.ButtonStyle.secondary,
            custom_id=f"leon_rating:{rating_id}:{rating}",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        record = database.get_rating(self.rating_id)
        if record is None:
            await interaction.response.send_message(
                "لم يتم العثور على طلب التقييم.",
                ephemeral=True,
            )
            return
        if interaction.user.id != record["owner_id"]:
            await interaction.response.send_message(
                "هذا التقييم ليس خاصاً بك.",
                ephemeral=True,
            )
            return
        if record["rating"] is not None:
            await interaction.response.send_message(
                "لقد قمت بتقييم هذه التذكرة مسبقاً.",
                ephemeral=True,
            )
            return
        await interaction.response.defer()
        if not database.submit_rating(
            self.rating_id,
            interaction.user.id,
            self.rating,
        ):
            await interaction.followup.send(
                "لم يتم تسجيل التقييم، ربما تم تقييمه مسبقاً.",
                ephemeral=True,
            )
            return
        point_record = database.get_rating(self.rating_id)
        ticket = database.get_ticket(record["ticket_channel_id"])
        if point_record is not None and ticket is not None:
            point_payload = dict(point_record)
            point_payload.update(
                guild_id=ticket["guild_id"],
                closed=ticket["closed"],
                ticket_staff_id=ticket["staff_id"],
            )
            try:
                await process_rating_points(point_payload)
            except (sqlite3.Error, discord.HTTPException) as exc:
                print(
                    f"[ADMIN TICKET POINTS ERROR] "
                    f"{type(exc).__name__}: {exc}",
                    flush=True,
                )
        database.log_event(
            record["ticket_channel_id"],
            "rating",
            interaction.user.id,
            str(self.rating),
        )
        await send_ticket_log(
            record["ticket_channel_id"],
            "تقييم تذكرة",
            [
                ("العضو", interaction.user.mention, True),
                ("الإداري", f"<@{record['staff_id']}>", True),
                ("التقييم", "⭐" * self.rating, False),
            ],
            color=discord.Color.gold(),
        )

        view = self.view
        if view is not None:
            for item in view.children:
                item.disabled = True
        await interaction.edit_original_response(
            content=f"تم تسجيل تقييمك: {'⭐' * self.rating}",
            view=view,
        )


class RatingView(discord.ui.View):
    """Persistent view restored from SQLite after a restart."""

    def __init__(self, rating_id: int, staff_id: int) -> None:
        super().__init__(timeout=None)
        for rating in range(1, 6):
            self.add_item(RatingButton(rating, rating_id, staff_id))

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item[Any],
    ) -> None:
        print(
            f"[RATING VIEW ERROR] item={getattr(item, 'custom_id', item)} "
            f"error={type(error).__name__}: {error}",
            flush=True,
        )
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "حدث خطأ أثناء تسجيل التقييم.",
                ephemeral=True,
            )


async def create_transcript(channel: discord.TextChannel) -> Path | None:
    """Write a complete channel transcript to a temporary local file."""
    path = DATABASE_FILE.parent / f"transcript_{channel.id}.txt"
    try:
        with path.open("w", encoding="utf-8") as file:
            file.write(f"LEON TICKET TRANSCRIPT\nChannel: {channel.name}\n")
            file.write(f"Channel ID: {channel.id}\n{'=' * 70}\n\n")
            async for message in channel.history(limit=None, oldest_first=True):
                file.write(
                    f"[{message.created_at.isoformat()}] "
                    f"{message.author} ({message.author.id})\n"
                )
                file.write(message.content or "[بدون نص]")
                file.write("\n")
                for attachment in message.attachments:
                    file.write(f"Attachment: {attachment.url}\n")
                file.write(f"\n{'-' * 70}\n")
        return path
    except (OSError, discord.HTTPException) as exc:
        print(f"[TRANSCRIPT ERROR] {type(exc).__name__}: {exc}", flush=True)
        return None


async def send_ticket_log(
    channel_id: int,
    title: str,
    fields: list[tuple[str, str, bool]],
    color: discord.Color = discord.Color.blurple(),
    transcript: Path | None = None,
) -> discord.Message | None:
    """Send a structured ticket event to the configured log channel."""
    try:
        ticket = database.get_ticket(channel_id)
        if ticket is None:
            print(f"[LOG ERROR] Ticket {channel_id} has no guild.", flush=True)
            return None
        settings = database.get_ticket_settings(ticket["guild_id"])
        if settings is None or not settings.ticket_log_channel_id:
            print(
                f"[LOG ERROR] Guild {ticket['guild_id']} has no log channel.",
                flush=True,
            )
            return None
        guild = bot.get_guild(ticket["guild_id"])
        if guild is None:
            print(
                f"[LOG ERROR] Guild {ticket['guild_id']} is unavailable.",
                flush=True,
            )
            return None
        log_channel = guild.get_channel(settings.ticket_log_channel_id)
        if log_channel is None:
            log_channel = await bot.fetch_channel(settings.ticket_log_channel_id)
        if getattr(log_channel, "guild", None) != guild:
            print(
                f"[LOG ERROR] Log channel is outside guild {guild.id}.",
                flush=True,
            )
            return None
        if not isinstance(log_channel, discord.abc.Messageable):
            return None
        embed = discord.Embed(title=title, color=color, timestamp=utc_now())
        for name, value, inline in fields:
            embed.add_field(name=name, value=value, inline=inline)
        message = await log_channel.send(
            embed=embed,
            file=discord.File(transcript) if transcript is not None else None,
        )
        if transcript is not None:
            database.mark_transcript(channel_id, message.id)
        return message
    except (discord.HTTPException, RuntimeError, sqlite3.Error) as exc:
        print(f"[LOG ERROR] {type(exc).__name__}: {exc}", flush=True)
        return None


async def send_close_notice(
    owner_id: int,
    ticket_name: str,
    reason: str,
    auto_closed: bool,
) -> None:
    """Notify the ticket owner even when no staff member claimed it."""
    try:
        user = await bot.fetch_user(owner_id)
        embed = discord.Embed(
            title="تم إغلاق تذكرتك",
            description=(
                f"التذكرة: **{ticket_name}**\n"
                f"السبب: **{reason}**\n"
                f"نوع الإغلاق: **{'تلقائي' if auto_closed else 'يدوي'}**"
            ),
            color=discord.Color.red(),
            timestamp=utc_now(),
        )
        await user.send(embed=embed)
    except discord.Forbidden:
        print(f"[CLOSE DM] DMs disabled for user {owner_id}.", flush=True)
    except discord.HTTPException as exc:
        print(f"[CLOSE DM ERROR] {exc}", flush=True)


async def send_rating_dm(
    owner_id: int,
    staff_id: int,
    ticket_channel_id: int,
    ticket_name: str,
) -> None:
    rating = database.create_rating(ticket_channel_id, owner_id, staff_id)
    try:
        user = await bot.fetch_user(owner_id)
        embed = discord.Embed(
            title="تقييم خدمة التذكرة",
            description=(
                f"تم إغلاق تذكرتك **{ticket_name}**.\n\n"
                f"الإداري المستلم: <@{staff_id}>\n\n"
                "اختر تقييم الخدمة من 1 إلى 5 نجوم:"
            ),
            color=discord.Color.gold(),
            timestamp=utc_now(),
        )
        await user.send(
            embed=embed,
            view=RatingView(rating["id"], staff_id),
        )
    except discord.Forbidden:
        print(f"[RATING DM] DMs disabled for user {owner_id}.", flush=True)
    except discord.HTTPException as exc:
        print(f"[RATING DM ERROR] {exc}", flush=True)


class CloseTicketModal(discord.ui.Modal):
    """Collect a required reason before a staff member closes a ticket."""

    def __init__(self) -> None:
        super().__init__(title="إغلاق التذكرة")
        self.reason = discord.ui.TextInput(
            label="سبب إغلاق التذكرة",
            placeholder="اكتب سبب إغلاق التذكرة...",
            required=True,
            min_length=3,
            max_length=500,
            style=discord.TextStyle.paragraph,
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        if not is_support_staff(interaction.user):
            await interaction.response.send_message(
                "هذا الزر مخصص لفريق الدعم.",
                ephemeral=True,
            )
            return
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                "لا يمكن إغلاق هذه القناة كتذكرة.",
                ephemeral=True,
            )
            return

        record = database.get_ticket(interaction.channel.id)
        if record is None or record["closed"]:
            await interaction.response.send_message(
                "هذه التذكرة غير موجودة أو مغلقة.",
                ephemeral=True,
            )
            return

        reason = str(self.reason.value).strip()
        await interaction.response.send_message(
            f"🔒 **تم طلب إغلاق التذكرة**\n"
            f"📝 السبب: {reason}\n\n"
            "سيتم حذف التذكرة خلال 5 ثوانٍ.",
        )
        await asyncio.sleep(5)
        await close_ticket(
            interaction.channel,
            reason=reason,
            closed_by=interaction.user.mention,
        )

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
    ) -> None:
        print(
            f"[CLOSE MODAL ERROR] {type(error).__name__}: {error}",
            flush=True,
        )
        if interaction.response.is_done():
            await interaction.followup.send(
                "حدث خطأ أثناء إغلاق التذكرة.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "حدث خطأ أثناء إغلاق التذكرة.",
                ephemeral=True,
            )


async def close_ticket(
    channel: discord.TextChannel,
    reason: str,
    closed_by: str,
    auto_closed: bool = False,
) -> None:
    """Close once, transcript, log, DM rating, then delete the channel."""
    record = database.close_ticket(
        channel.id,
        reason,
        closed_by,
        auto_closed=auto_closed,
    )
    if record is None:
        return

    database.log_event(
        channel.id,
        "auto_close" if auto_closed else "close",
        details=reason,
    )
    transcript = await create_transcript(channel)
    try:
        await send_ticket_log(
            channel.id,
            "تم إغلاق تذكرة",
            [
                ("التذكرة", channel.name, True),
                ("العضو", f"<@{record['owner_id']}>", True),
                (
                    "الإداري",
                    f"<@{record['staff_id']}>"
                    if record["staff_id"]
                    else "لم يتم الاستلام",
                    True,
                ),
                ("السبب", reason, False),
                ("أغلقها", closed_by, False),
                ("النوع", "تلقائي" if auto_closed else "يدوي", True),
            ],
            color=discord.Color.red(),
            transcript=transcript,
        )
    finally:
        if transcript is not None:
            transcript.unlink(missing_ok=True)

    await send_close_notice(
        record["owner_id"],
        channel.name,
        reason,
        auto_closed,
    )
    if record["staff_id"]:
        try:
            await send_rating_dm(
                record["owner_id"],
                record["staff_id"],
                channel.id,
                channel.name,
            )
        except Exception as exc:
            print(
                f"[RATING DELIVERY ERROR] {type(exc).__name__}: {exc}",
                flush=True,
            )

    try:
        await channel.delete(reason=reason)
        database.log_event(channel.id, "delete", details=reason)
    except discord.HTTPException as exc:
        print(f"[DELETE TICKET ERROR] {exc}", flush=True)


def parse_discord_user_id(value: str) -> int | None:
    """Accept a raw Discord user ID or a member mention."""
    match = re.fullmatch(r"\s*<@!?(\d+)>\s*|\s*(\d+)\s*", value)
    if match is None:
        return None
    return int(match.group(1) or match.group(2))


class TicketMemberModal(discord.ui.Modal):
    """Add or remove a member from a private ticket channel."""

    def __init__(self, action: str) -> None:
        self.action = action
        title = "إضافة عضو للتذكرة" if action == "add" else "إزالة عضو من التذكرة"
        super().__init__(title=title)
        self.member_value = discord.ui.TextInput(
            label="معرف العضو أو المنشن",
            placeholder="123456789012345678 أو @user",
            required=True,
            min_length=2,
            max_length=40,
        )
        self.add_item(self.member_value)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if (
            interaction.guild is None
            or not isinstance(interaction.channel, discord.TextChannel)
            or not is_support_staff(interaction.user)
        ):
            await interaction.response.send_message(
                "لا تملك صلاحية إدارة أعضاء التذكرة.",
                ephemeral=True,
            )
            return
        record = database.get_ticket(interaction.channel.id)
        if record is None or record["closed"]:
            await interaction.response.send_message(
                "هذه التذكرة غير موجودة أو مغلقة.",
                ephemeral=True,
            )
            return
        member_id = parse_discord_user_id(str(self.member_value.value))
        if member_id is None:
            await interaction.response.send_message(
                "أدخل منشن صحيحاً أو Discord ID رقمياً.",
                ephemeral=True,
            )
            return
        if self.action == "remove" and member_id == record["owner_id"]:
            await interaction.response.send_message(
                "لا يمكن إزالة صاحب التذكرة.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            member = interaction.guild.get_member(member_id)
            if member is None:
                member = await interaction.guild.fetch_member(member_id)
            if self.action == "add":
                await interaction.channel.set_permissions(
                    member,
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    attach_files=True,
                    reason=f"Added by {interaction.user}",
                )
                event_type = "member_add"
                label = "إضافة عضو"
            else:
                await interaction.channel.set_permissions(
                    member,
                    overwrite=None,
                    reason=f"Removed by {interaction.user}",
                )
                event_type = "member_remove"
                label = "إزالة عضو"
            database.log_event(
                interaction.channel.id,
                event_type,
                interaction.user.id,
                str(member.id),
            )
            await send_ticket_log(
                interaction.channel.id,
                label,
                [
                    ("التذكرة", interaction.channel.name, True),
                    ("العضو", member.mention, True),
                    ("بواسطة", interaction.user.mention, True),
                ],
            )
            await interaction.followup.send(
                f"تمت {label} بنجاح: {member.mention}",
                ephemeral=True,
            )
        except (discord.NotFound, discord.Forbidden):
            await interaction.followup.send(
                "تعذر العثور على العضو أو تعديل صلاحياته.",
                ephemeral=True,
            )


class TicketControlView(discord.ui.View):
    """Persistent controls available inside every ticket channel."""

    def __init__(self, legacy: bool = False) -> None:
        super().__init__(timeout=None)
        if legacy:
            for item in self.children:
                if item.custom_id in LEGACY_COMPONENT_IDS:
                    item.custom_id = LEGACY_COMPONENT_IDS[item.custom_id]

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item[Any],
    ) -> None:
        print(
            f"[VIEW ERROR] item={getattr(item, 'custom_id', item)} "
            f"error={type(error).__name__}: {error}",
            flush=True,
        )
        if interaction.response.is_done():
            await interaction.followup.send(
                "حدث خطأ أثناء تنفيذ العملية.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "حدث خطأ أثناء تنفيذ العملية.",
                ephemeral=True,
            )

    @discord.ui.button(
        label="استلام التذكرة",
        style=discord.ButtonStyle.primary,
        custom_id="leon_ticket:claim",
        row=0,
    )
    async def claim_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["TicketControlView"],
    ) -> None:
        if interaction.guild is None:
            return
        if not is_support_staff(interaction.user):
            await interaction.response.send_message(
                "هذا الزر مخصص لفريق الدعم.",
                ephemeral=True,
            )
            return
        if not isinstance(interaction.channel, discord.TextChannel):
            return

        record = database.get_ticket(interaction.channel.id)
        if record is None or record["closed"]:
            await interaction.response.send_message(
                "هذه التذكرة غير موجودة أو مغلقة.",
                ephemeral=True,
            )
            return
        if record["staff_id"]:
            message = (
                "أنت مستلم هذه التذكرة بالفعل."
                if record["staff_id"] == interaction.user.id
                else f"التذكرة مستلمة من قبل <@{record['staff_id']}>."
            )
            await interaction.response.send_message(message, ephemeral=True)
            return
        if not database.claim_ticket(
            interaction.channel.id,
            interaction.user.id,
        ):
            await interaction.response.send_message(
                "تم استلام التذكرة من إداري آخر.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"تم استلام التذكرة بواسطة {interaction.user.mention}.",
        )
        database.log_event(
            interaction.channel.id,
            "claim",
            interaction.user.id,
        )
        await send_ticket_log(
            interaction.channel.id,
            "استلام تذكرة",
            [
                ("التذكرة", interaction.channel.name, True),
                ("الإداري", interaction.user.mention, True),
            ],
        )

    @discord.ui.button(
        label="إلغاء الاستلام",
        style=discord.ButtonStyle.secondary,
        custom_id="leon_ticket:unclaim",
        row=0,
    )
    async def unclaim_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["TicketControlView"],
    ) -> None:
        if (
            not isinstance(interaction.channel, discord.TextChannel)
            or not is_support_staff(interaction.user)
        ):
            await interaction.response.send_message(
                "هذا الزر مخصص لفريق الدعم.",
                ephemeral=True,
            )
            return
        if not database.unclaim_ticket(
            interaction.channel.id,
            interaction.user.id,
        ):
            await interaction.response.send_message(
                "لا يمكنك إلغاء استلام تذكرة استلمها إداري آخر.",
                ephemeral=True,
            )
            return
        database.log_event(
            interaction.channel.id,
            "unclaim",
            interaction.user.id,
        )
        await interaction.response.send_message(
            f"ألغى {interaction.user.mention} استلام التذكرة."
        )
        await send_ticket_log(
            interaction.channel.id,
            "إلغاء استلام تذكرة",
            [
                ("التذكرة", interaction.channel.name, True),
                ("الإداري", interaction.user.mention, True),
            ],
        )

    @discord.ui.button(
        label="تذكير العضو",
        style=discord.ButtonStyle.secondary,
        custom_id="leon_ticket:remind_member",
        row=0,
    )
    async def remind_member(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["TicketControlView"],
    ) -> None:
        if interaction.guild is None:
            return
        if not is_support_staff(interaction.user):
            await interaction.response.send_message(
                "هذا الزر مخصص لفريق الدعم.",
                ephemeral=True,
            )
            return
        if not isinstance(interaction.channel, discord.TextChannel):
            return

        record = database.get_ticket(interaction.channel.id)
        if record is None or record["closed"]:
            await interaction.response.send_message(
                "هذه التذكرة غير موجودة أو مغلقة.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        if not database.claim_reminder(
            interaction.channel.id,
            "member_reminded",
        ):
            await interaction.followup.send(
                "تم إرسال تذكير للعضو مسبقاً، ولا يمكن إرساله مرة أخرى في هذه التذكرة.",
                ephemeral=True,
            )
            return

        try:
            member = await bot.fetch_user(record["owner_id"])
            embed = discord.Embed(
                title="تذكير بتذكرتك",
                description=(
                    "مرحباً 👋\n\n"
                    "لا تزال لديك **تذكرة مفتوحة** في السيرفر.\n\n"
                    f"التذكرة: `{interaction.channel.name}`\n\n"
                    "يرجى العودة إلى التذكرة والرد على فريق الدعم."
                ),
                color=discord.Color.orange(),
                timestamp=utc_now(),
            )
            embed.set_footer(text="LEON Support")
            await member.send(embed=embed)
            database.mark_reminder_sent(
                interaction.channel.id,
                "member_reminded",
                "member_reminded_at",
            )
            database.log_event(
                interaction.channel.id,
                "remind_member",
                interaction.user.id,
            )
            await send_ticket_log(
                interaction.channel.id,
                "تذكير العضو",
                [
                    ("التذكرة", interaction.channel.name, True),
                    ("بواسطة", interaction.user.mention, True),
                    ("العضو", f"<@{record['owner_id']}>", True),
                ],
                color=discord.Color.orange(),
            )
        except discord.Forbidden:
            database.release_reminder(
                interaction.channel.id,
                "member_reminded",
            )
            await interaction.followup.send(
                "الرسائل الخاصة بالعضو مغلقة، لذلك لم يتم احتساب التذكير.",
                ephemeral=True,
            )
            return
        except discord.HTTPException as exc:
            database.release_reminder(
                interaction.channel.id,
                "member_reminded",
            )
            print(f"[REMIND MEMBER ERROR] {exc}", flush=True)
            await interaction.followup.send(
                "حدث خطأ أثناء إرسال التذكير.",
                ephemeral=True,
            )
            return
        except Exception as exc:
            database.release_reminder(
                interaction.channel.id,
                "member_reminded",
            )
            print(f"[REMIND MEMBER ERROR] {type(exc).__name__}: {exc}", flush=True)
            await interaction.followup.send(
                "حدث خطأ أثناء إرسال التذكير.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            "تم إرسال التذكير للعضو في الخاص. لا يمكن إرسال تذكير آخر في هذه التذكرة.",
            ephemeral=True,
        )

    @discord.ui.button(
        label="تذكير الإداري",
        style=discord.ButtonStyle.secondary,
        custom_id="leon_ticket:remind_staff",
        row=0,
    )
    async def remind_staff(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["TicketControlView"],
    ) -> None:
        if interaction.guild is None:
            return
        if not isinstance(interaction.channel, discord.TextChannel):
            return

        record = database.get_ticket(interaction.channel.id)
        if record is None or record["closed"]:
            await interaction.response.send_message(
                "هذه التذكرة غير موجودة أو مغلقة.",
                ephemeral=True,
            )
            return
        if interaction.user.id != record["owner_id"]:
            await interaction.response.send_message(
                "هذا الزر مخصص لصاحب التذكرة.",
                ephemeral=True,
            )
            return
        if not record["staff_id"]:
            await interaction.response.send_message(
                "لم يستلم أي إداري التذكرة حتى الآن.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        if not database.claim_reminder(
            interaction.channel.id,
            "staff_reminded",
        ):
            await interaction.followup.send(
                "تم إرسال تذكير للإداري مسبقاً، ولا يمكن إرساله مرة أخرى في هذه التذكرة.",
                ephemeral=True,
            )
            return

        try:
            staff = await bot.fetch_user(record["staff_id"])
            embed = discord.Embed(
                title="تذكير بتذكرة مفتوحة",
                description=(
                    "مرحباً 👋\n\n"
                    "لديك تذكرة مفتوحة لا تزال بحاجة إلى المتابعة.\n\n"
                    f"التذكرة: `{interaction.channel.name}`\n\n"
                    f"العضو: {interaction.user.mention}\n\n"
                    "يرجى العودة إلى التذكرة ومتابعة العضو."
                ),
                color=discord.Color.blurple(),
                timestamp=utc_now(),
            )
            embed.set_footer(text="LEON Support")
            await staff.send(embed=embed)
            database.mark_reminder_sent(
                interaction.channel.id,
                "staff_reminded",
                "staff_reminded_at",
            )
            database.log_event(
                interaction.channel.id,
                "remind_staff",
                interaction.user.id,
                str(record["staff_id"]),
            )
            await send_ticket_log(
                interaction.channel.id,
                "تذكير الإداري",
                [
                    ("التذكرة", interaction.channel.name, True),
                    ("العضو", interaction.user.mention, True),
                    ("الإداري", f"<@{record['staff_id']}>", True),
                ],
            )
        except discord.Forbidden:
            database.release_reminder(
                interaction.channel.id,
                "staff_reminded",
            )
            await interaction.followup.send(
                "الرسائل الخاصة بالإداري مغلقة، لذلك لم يتم احتساب التذكير.",
                ephemeral=True,
            )
            return
        except discord.HTTPException as exc:
            database.release_reminder(
                interaction.channel.id,
                "staff_reminded",
            )
            print(f"[REMIND STAFF ERROR] {exc}", flush=True)
            await interaction.followup.send(
                "حدث خطأ أثناء إرسال التذكير.",
                ephemeral=True,
            )
            return
        except Exception as exc:
            database.release_reminder(
                interaction.channel.id,
                "staff_reminded",
            )
            print(f"[REMIND STAFF ERROR] {type(exc).__name__}: {exc}", flush=True)
            await interaction.followup.send(
                "حدث خطأ أثناء إرسال التذكير.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            "تم إرسال التذكير للإداري في الخاص. لا يمكن إرسال تذكير آخر في هذه التذكرة.",
            ephemeral=True,
        )

    @discord.ui.button(
        label="إضافة عضو",
        style=discord.ButtonStyle.success,
        custom_id="leon_ticket:add_member",
        row=1,
    )
    async def add_member(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["TicketControlView"],
    ) -> None:
        if not is_support_staff(interaction.user):
            await interaction.response.send_message(
                "هذا الزر مخصص لفريق الدعم.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(TicketMemberModal("add"))

    @discord.ui.button(
        label="إزالة عضو",
        style=discord.ButtonStyle.secondary,
        custom_id="leon_ticket:remove_member",
        row=1,
    )
    async def remove_member(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["TicketControlView"],
    ) -> None:
        if not is_support_staff(interaction.user):
            await interaction.response.send_message(
                "هذا الزر مخصص لفريق الدعم.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(TicketMemberModal("remove"))

    @discord.ui.button(
        label="Transcript",
        style=discord.ButtonStyle.secondary,
        custom_id="leon_ticket:transcript",
        row=1,
    )
    async def transcript_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["TicketControlView"],
    ) -> None:
        if (
            not isinstance(interaction.channel, discord.TextChannel)
            or not is_support_staff(interaction.user)
        ):
            await interaction.response.send_message(
                "هذا الزر مخصص لفريق الدعم.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        transcript = await create_transcript(interaction.channel)
        if transcript is None:
            await interaction.followup.send(
                "تعذر إنشاء Transcript.",
                ephemeral=True,
            )
            return
        try:
            await send_ticket_log(
                interaction.channel.id,
                "إنشاء Transcript",
                [
                    ("التذكرة", interaction.channel.name, True),
                    ("بواسطة", interaction.user.mention, True),
                ],
                transcript=transcript,
            )
            database.log_event(
                interaction.channel.id,
                "transcript",
                interaction.user.id,
            )
            await interaction.followup.send(
                "تم إنشاء Transcript وإرساله إلى سجل التذاكر.",
                ephemeral=True,
            )
        finally:
            transcript.unlink(missing_ok=True)

    @discord.ui.button(
        label="إغلاق التذكرة",
        style=discord.ButtonStyle.danger,
        custom_id="leon_ticket:close",
        row=1,
    )
    async def close_ticket_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["TicketControlView"],
    ) -> None:
        if interaction.guild is None:
            return
        if not is_support_staff(interaction.user):
            await interaction.response.send_message(
                "فريق الدعم فقط يستطيع إغلاق التذكرة.",
                ephemeral=True,
            )
            return
        if not isinstance(interaction.channel, discord.TextChannel):
            return

        await interaction.response.send_modal(CloseTicketModal())


async def send_submission_decision_dm(
    owner_id: int,
    submission_type: str,
    decision: str,
    staff: discord.Member,
) -> None:
    label = TICKET_TYPES[submission_type][0]
    result = "تم قبول طلبك ✅" if decision == "accepted" else "تم رفض طلبك ❌"
    try:
        user = await bot.fetch_user(owner_id)
        embed = discord.Embed(
            title=f"نتيجة {label}",
            description=(
                f"{result}\n\n"
                f"تمت معالجة الطلب بواسطة: {staff.mention}"
            ),
            color=(
                discord.Color.green()
                if decision == "accepted"
                else discord.Color.red()
            ),
            timestamp=utc_now(),
        )
        await user.send(embed=embed)
    except discord.Forbidden:
        print(f"[SUBMISSION DM] DMs disabled for user {owner_id}.", flush=True)
    except discord.HTTPException as exc:
        print(f"[SUBMISSION DM ERROR] {exc}", flush=True)


class SubmissionDecisionView(discord.ui.View):
    """Persistent accept/reject controls for applications and partnerships."""

    def __init__(self, submission_type: str | None = None) -> None:
        super().__init__(timeout=None)
        if submission_type == "admin_application":
            self.accept.label = "قبول التقديم"
            self.reject.label = "رفض التقديم"
        elif submission_type == "partnership":
            self.accept.label = "قبول الشراكة"
            self.reject.label = "رفض الشراكة"

    async def decide(
        self,
        interaction: discord.Interaction,
        decision: str,
    ) -> None:
        if (
            interaction.guild is None
            or not isinstance(interaction.channel, discord.TextChannel)
            or not isinstance(interaction.user, discord.Member)
            or not is_support_staff(interaction.user)
        ):
            await interaction.response.send_message(
                "هذا القرار مخصص لفريق الإدارة.",
                ephemeral=True,
            )
            return
        record = database.get_ticket(interaction.channel.id)
        submission = database.get_submission(interaction.channel.id)
        if (
            record is None
            or record["closed"]
            or submission is None
            or submission["submission_type"] not in SUBMISSION_TYPES
        ):
            await interaction.response.send_message(
                "هذه ليست تذكرة تقديم أو شراكة مفتوحة.",
                ephemeral=True,
            )
            return
        if not database.decide_submission(
            interaction.channel.id,
            decision,
            interaction.user.id,
        ):
            current = database.get_submission(interaction.channel.id)
            result = (
                "مقبول"
                if current is not None and current["decision"] == "accepted"
                else "مرفوض"
            )
            await interaction.response.send_message(
                f"تم اتخاذ قرار سابق على هذا الطلب: **{result}**.",
                ephemeral=True,
            )
            return

        accepted = decision == "accepted"
        type_label = TICKET_TYPES[submission["submission_type"]][0]
        decision_label = "قبول" if accepted else "رفض"
        database.log_event(
            interaction.channel.id,
            f"{submission['submission_type']}_{decision}",
            interaction.user.id,
        )
        await interaction.response.send_message(
            f"{'✅' if accepted else '❌'} تم **{decision_label}** "
            f"{type_label} بواسطة {interaction.user.mention}."
        )
        await send_ticket_log(
            interaction.channel.id,
            f"{decision_label} {type_label}",
            [
                ("التذكرة", interaction.channel.name, True),
                ("صاحب الطلب", f"<@{record['owner_id']}>", True),
                ("القرار", decision_label, True),
                ("بواسطة", interaction.user.mention, True),
            ],
            color=discord.Color.green() if accepted else discord.Color.red(),
        )
        await send_submission_decision_dm(
            record["owner_id"],
            submission["submission_type"],
            decision,
            interaction.user,
        )
        for item in self.children:
            item.disabled = True
        try:
            await interaction.message.edit(view=self)
        except discord.HTTPException:
            pass

    @discord.ui.button(
        label="قبول الطلب",
        style=discord.ButtonStyle.success,
        custom_id="leon_ticket:decision:accept",
    )
    async def accept(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["SubmissionDecisionView"],
    ) -> None:
        await self.decide(interaction, "accepted")

    @discord.ui.button(
        label="رفض الطلب",
        style=discord.ButtonStyle.danger,
        custom_id="leon_ticket:decision:reject",
    )
    async def reject(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button["SubmissionDecisionView"],
    ) -> None:
        await self.decide(interaction, "rejected")


class AdminApplicationModal(discord.ui.Modal):
    """Collect an administration application within Discord's five-field limit."""

    def __init__(self) -> None:
        super().__init__(title="👮 تقديم إدارة")
        self.name = discord.ui.TextInput(
            label="الاسم",
            max_length=100,
        )
        self.age = discord.ui.TextInput(
            label="العمر",
            max_length=3,
        )
        self.experience = discord.ui.TextInput(
            label="الخبرة وهل عملت بإدارة سيرفر سابقاً؟",
            style=discord.TextStyle.paragraph,
            max_length=1000,
        )
        self.position = discord.ui.TextInput(
            label="الرتبة أو المنصب المطلوب",
            max_length=100,
        )
        self.details = discord.ui.TextInput(
            label="سبب الانضمام، ساعات التواجد، ومعلومات إضافية",
            style=discord.TextStyle.paragraph,
            placeholder=(
                "لماذا تريد الانضمام؟ كم ساعة تتواجد يومياً؟ "
                "أضف أي معلومات أخرى."
            ),
            max_length=1000,
        )
        for item in (
            self.name,
            self.age,
            self.experience,
            self.position,
            self.details,
        ):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        responses = {
            "الاسم": str(self.name.value),
            "العمر": str(self.age.value),
            "الخبرة والعمل الإداري السابق": str(self.experience.value),
            "الرتبة أو المنصب المطلوب": str(self.position.value),
            "سبب الانضمام وساعات التواجد ومعلومات إضافية": str(
                self.details.value
            ),
        }
        await interaction.response.defer(ephemeral=True)
        await create_ticket_for_interaction(
            interaction,
            "admin_application",
            responses,
        )


class PartnershipModal(discord.ui.Modal):
    """Collect partnership details within Discord's five-field limit."""

    def __init__(self) -> None:
        super().__init__(title="🤝 طلب شراكة")
        self.entity_name = discord.ui.TextInput(
            label="اسم السيرفر أو الجهة",
            max_length=100,
        )
        self.invite_url = discord.ui.TextInput(
            label="رابط السيرفر",
            placeholder="https://discord.gg/...",
            max_length=300,
        )
        self.member_count = discord.ui.TextInput(
            label="عدد الأعضاء",
            max_length=20,
        )
        self.activity = discord.ui.TextInput(
            label="نوع المحتوى أو النشاط",
            style=discord.TextStyle.paragraph,
            max_length=500,
        )
        self.details = discord.ui.TextInput(
            label="ما تقدمه وتريده ومعلومات إضافية",
            style=discord.TextStyle.paragraph,
            placeholder=(
                "ما الذي تقدمه الشراكة؟ ماذا تريد من شراكتنا؟ "
                "أضف أي معلومات أخرى."
            ),
            max_length=1000,
        )
        for item in (
            self.entity_name,
            self.invite_url,
            self.member_count,
            self.activity,
            self.details,
        ):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        responses = {
            "اسم السيرفر أو الجهة": str(self.entity_name.value),
            "رابط السيرفر": str(self.invite_url.value),
            "عدد الأعضاء": str(self.member_count.value),
            "نوع المحتوى أو النشاط": str(self.activity.value),
            "ما تقدمه الشراكة وما تريده ومعلومات إضافية": str(
                self.details.value
            ),
        }
        await interaction.response.defer(ephemeral=True)
        await create_ticket_for_interaction(
            interaction,
            "partnership",
            responses,
        )


async def create_ticket_for_interaction(
    interaction: discord.Interaction,
    ticket_type: str,
    responses: dict[str, str] | None = None,
) -> None:
    """Create every ticket type through one duplicate-safe shared path."""
    if interaction.guild is None or not isinstance(
        interaction.user,
        discord.Member,
    ):
        return
    settings = database.get_ticket_settings(interaction.guild.id)
    if settings is None:
        await interaction.followup.send(
            "إعدادات التذاكر لهذا السيرفر غير مكتملة. "
            "استخدم `/setup_ticket` أولاً.",
            ephemeral=True,
        )
        return
    if not settings.ticket_category_id:
        try:
            category = await interaction.guild.create_category(
                "LEON Tickets",
                reason=f"Automatic ticket setup for guild {interaction.guild.id}",
            )
            database.save_ticket_settings(
                interaction.guild.id,
                category.id,
                settings.ticket_log_channel_id,
                settings.support_role_id,
            )
            settings = database.get_ticket_settings(interaction.guild.id)
        except discord.HTTPException as exc:
            print(f"[CREATE CATEGORY ERROR] {exc}", flush=True)
            await interaction.followup.send(
                "تعذر إنشاء Category مستقلة لتذاكر هذا السيرفر.",
                ephemeral=True,
            )
            return
    if settings is None:
        return
    category = interaction.guild.get_channel(settings.ticket_category_id)
    if not isinstance(category, discord.CategoryChannel):
        await interaction.followup.send(
            "Category التذاكر المحفوظة لا تنتمي لهذا السيرفر أو لم تعد موجودة. "
            "أعد تنفيذ `/setup_ticket`.",
            ephemeral=True,
        )
        return

    existing = database.get_open_ticket_for_owner(
        interaction.guild.id,
        interaction.user.id,
    )
    if existing is not None:
        existing_channel = interaction.guild.get_channel(existing["channel_id"])
        if existing_channel is not None:
            await interaction.followup.send(
                f"لديك تذكرة مفتوحة بالفعل: {existing_channel.mention}",
                ephemeral=True,
            )
            return
    if not database.reserve_open_ticket(
        interaction.guild.id,
        interaction.user.id,
    ):
        await interaction.followup.send(
            "يوجد طلب فتح تذكرة قيد التنفيذ أو تذكرة مفتوحة بالفعل.",
            ephemeral=True,
        )
        return

    overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
        interaction.guild.default_role: discord.PermissionOverwrite(
            view_channel=False
        ),
        interaction.user: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            attach_files=True,
        ),
    }
    if interaction.guild.me is not None:
        overwrites[interaction.guild.me] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            attach_files=True,
            manage_channels=True,
            manage_messages=True,
        )
    for role in interaction.guild.roles:
        if (
            role != interaction.guild.default_role
            and role.permissions.manage_channels
        ):
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
            )
    if settings.support_role_id:
        support_role = interaction.guild.get_role(settings.support_role_id)
        if support_role is not None:
            overwrites[support_role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
            )

    ticket_label = TICKET_TYPES[ticket_type][0]
    safe_name = re.sub(
        r"[^a-zA-Z0-9_\-\u0600-\u06ff]",
        "-",
        interaction.user.name,
    )
    prefix = (
        "تقديم"
        if ticket_type == "admin_application"
        else "شراكة" if ticket_type == "partnership" else "تكت"
    )
    channel_name = f"{prefix}-{safe_name}".lower()[:90]

    ticket_channel: discord.TextChannel | None = None
    try:
        ticket_channel = await category.create_text_channel(
            name=channel_name,
            overwrites=overwrites,
            reason="LEON Ticket Created",
        )
        database.create_ticket(
            ticket_channel.id,
            interaction.guild.id,
            interaction.user.id,
            ticket_type,
            responses,
        )
        database.finalize_open_ticket(
            interaction.guild.id,
            interaction.user.id,
            ticket_channel.id,
        )
        embed = discord.Embed(
            title="LEON SUPPORT",
            description=(
                f"أهلاً بك {interaction.user.mention}.\n\n"
                f"نوع الطلب: **{ticket_label}**\n\n"
                "اكتب أي تفاصيل إضافية وسيتم الرد عليك من فريق الدعم.\n"
                "يمكن لفريق الدعم استلام التذكرة أو إغلاقها من الأزرار."
            ),
            color=discord.Color.green(),
        )
        await ticket_channel.send(
            content=interaction.user.mention,
            embed=embed,
            view=TicketControlView(),
        )
        if responses is not None:
            response_embed = discord.Embed(
                title=f"بيانات {ticket_label}",
                color=discord.Color.blurple(),
                timestamp=utc_now(),
            )
            for question, answer in responses.items():
                response_embed.add_field(
                    name=question,
                    value=answer[:1024] or "لم تتم الإجابة",
                    inline=False,
                )
            await ticket_channel.send(embed=response_embed)
            await ticket_channel.send(
                content="قرار الإدارة:",
                view=SubmissionDecisionView(ticket_type),
            )
        database.log_event(
            ticket_channel.id,
            "open",
            interaction.user.id,
            ticket_type,
        )
        await send_ticket_log(
            ticket_channel.id,
            "فتح تذكرة",
            [
                ("التذكرة", ticket_channel.name, True),
                ("العضو", interaction.user.mention, True),
                ("النوع", ticket_label, True),
            ],
            color=discord.Color.green(),
        )
    except (discord.HTTPException, sqlite3.Error) as exc:
        print(f"[CREATE TICKET ERROR] {type(exc).__name__}: {exc}", flush=True)
        database.release_open_ticket(
            interaction.guild.id,
            interaction.user.id,
        )
        if ticket_channel is not None:
            try:
                await ticket_channel.delete(
                    reason="Ticket creation did not complete"
                )
            except discord.HTTPException:
                pass
        await interaction.followup.send(
            "فشل إنشاء التذكرة. تأكد من صلاحيات البوت.",
            ephemeral=True,
        )
        return

    await interaction.followup.send(
        f"تم إنشاء تذكرتك بنجاح: {ticket_channel.mention}",
        ephemeral=True,
    )


class SupportSelect(discord.ui.Select):
    """Persistent ticket type selector."""

    def __init__(self, legacy: bool = False) -> None:
        options = [
            discord.SelectOption(
                label=label,
                value=value,
                description=description,
            )
            for value, (label, description) in TICKET_TYPES.items()
        ]
        super().__init__(
            placeholder="اختر نوع التذكرة...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=LEGACY_SELECT_ID if legacy else "leon_ticket:type",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(
            interaction.user,
            discord.Member,
        ):
            return
        ticket_type = self.values[0]
        if ticket_type == "admin_application":
            await interaction.response.send_modal(AdminApplicationModal())
            return
        if ticket_type == "partnership":
            await interaction.response.send_modal(PartnershipModal())
            return
        await interaction.response.defer(ephemeral=True)
        await create_ticket_for_interaction(interaction, ticket_type)


class TicketLauncher(discord.ui.View):
    """Persistent view used by the setup slash command."""

    def __init__(self, legacy: bool = False) -> None:
        super().__init__(timeout=None)
        self.add_item(SupportSelect(legacy=legacy))
        self.add_item(OpenTicketButton(legacy=legacy))

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item[Any],
    ) -> None:
        print(
            f"[LAUNCHER ERROR] item={getattr(item, 'custom_id', item)} "
            f"error={type(error).__name__}: {error}",
            flush=True,
        )
        if interaction.response.is_done():
            await interaction.followup.send(
                "حدث خطأ أثناء إنشاء التذكرة.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "حدث خطأ أثناء إنشاء التذكرة.",
                ephemeral=True,
            )


class OpenTicketButton(discord.ui.Button["TicketLauncher"]):
    """Open an ephemeral type picker without removing the existing select."""

    def __init__(self, legacy: bool = False) -> None:
        super().__init__(
            label="فتح تذكرة",
            style=discord.ButtonStyle.success,
            custom_id=(
                "legacy_ticket:open" if legacy else "leon_ticket:open"
            ),
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = discord.ui.View(timeout=120)
        view.add_item(SupportSelect())
        await interaction.response.send_message(
            "اختر نوع التذكرة من القائمة:",
            view=view,
            ephemeral=True,
        )


@bot.tree.command(name="setup_ticket", description="إنشاء لوحة التذاكر")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(
    category="Category التذاكر لهذا السيرفر (اختياري)",
    log_channel="روم سجلات التذاكر لهذا السيرفر (اختياري)",
    support_role="رتبة الدعم لهذا السيرفر (اختياري)",
)
async def setup_ticket(
    interaction: discord.Interaction,
    category: discord.CategoryChannel | None = None,
    log_channel: discord.TextChannel | None = None,
    support_role: discord.Role | None = None,
) -> None:
    if interaction.channel is None or interaction.guild is None:
        return
    await interaction.response.defer(ephemeral=True)
    current = database.get_ticket_settings(interaction.guild.id)

    selected_category = category
    if selected_category is None and current and current.ticket_category_id:
        saved_category = interaction.guild.get_channel(current.ticket_category_id)
        if isinstance(saved_category, discord.CategoryChannel):
            selected_category = saved_category
    if selected_category is None:
        try:
            selected_category = await interaction.guild.create_category(
                "LEON Tickets",
                reason=f"Ticket setup by {interaction.user}",
            )
        except discord.HTTPException as exc:
            print(f"[SETUP CATEGORY ERROR] {exc}", flush=True)
            await interaction.followup.send(
                "تعذر إنشاء Category خاصة بتذاكر هذا السيرفر.",
                ephemeral=True,
            )
            return

    selected_log = log_channel
    if selected_log is None and current and current.ticket_log_channel_id:
        saved_log = interaction.guild.get_channel(current.ticket_log_channel_id)
        if isinstance(saved_log, discord.TextChannel):
            selected_log = saved_log
    if selected_log is None:
        await interaction.followup.send(
            "حدد روم السجلات عند استخدام `/setup_ticket` لأول مرة في هذا السيرفر.",
            ephemeral=True,
        )
        return

    selected_role_id = (
        support_role.id
        if support_role is not None
        else current.support_role_id if current else 0
    )
    database.save_ticket_settings(
        interaction.guild.id,
        selected_category.id,
        selected_log.id,
        selected_role_id,
    )
    embed = discord.Embed(
        title="LEON SUPPORT CENTER",
        description=(
            "اختر نوع التذكرة المناسب من القائمة بالأسفل.\n\n"
            "ستكون القناة خاصة بك وفريق الدعم، وتغلق تلقائياً بعد "
            f"{INACTIVITY_HOURS} ساعة من آخر رسالة للعضو."
        ),
        color=discord.Color.blurple(),
    )
    await interaction.channel.send(embed=embed, view=TicketLauncher())
    await interaction.followup.send(
        "تم حفظ إعدادات هذا السيرفر وإرسال لوحة التذاكر.\n"
        f"Category: `{selected_category.id}`\n"
        f"Logs: `{selected_log.id}`",
        ephemeral=True,
    )


@bot.tree.command(name="staff_stats", description="عرض إحصائيات إداري")
@app_commands.describe(staff="منشن الإداري")
async def staff_stats(
    interaction: discord.Interaction,
    staff: discord.Member,
) -> None:
    if interaction.guild is None:
        return
    if not is_support_staff(interaction.user):
        await interaction.response.send_message(
            "هذا الأمر مخصص لفريق الدعم.",
            ephemeral=True,
        )
        return

    stats = database.staff_statistics(staff.id)
    count = stats["ratings_count"]
    average = stats["average"]
    if not count:
        level = "لم يحصل على تقييمات بعد"
    elif average >= 4.5:
        level = "ممتاز جداً"
    elif average >= 4:
        level = "ممتاز"
    elif average >= 3:
        level = "جيد"
    elif average >= 2:
        level = "يحتاج إلى تحسين"
    else:
        level = "ضعيف"

    embed = discord.Embed(
        title="إحصائيات الإداري",
        color=discord.Color.gold(),
    )
    embed.add_field(name="الإداري", value=staff.mention, inline=False)
    embed.add_field(name="التذاكر المغلقة", value=str(stats["closed"]), inline=True)
    embed.add_field(name="عدد التقييمات", value=str(count), inline=True)
    embed.add_field(
        name="إجمالي النجوم",
        value=str(stats["total_rating"]),
        inline=True,
    )
    embed.add_field(
        name="متوسط التقييم",
        value=f"{average:.2f} / 5" if count else "لا يوجد",
        inline=True,
    )
    embed.add_field(
        name="توزيع التقييمات",
        value="\n".join(
            f"{'⭐' * star}: {stats['stars'][star]}"
            for star in range(5, 0, -1)
        ),
        inline=False,
    )
    embed.add_field(name="مستوى الإداري", value=level, inline=False)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(
    name="staff_stats_all",
    description="عرض إحصائيات جميع موظفي الإدارة",
)
async def staff_stats_all(interaction: discord.Interaction) -> None:
    if interaction.guild is None or not is_support_staff(interaction.user):
        await interaction.response.send_message(
            "هذا الأمر مخصص لمن لديه صلاحية Manage Channels.",
            ephemeral=True,
        )
        return
    rows = database.all_staff_statistics()
    if not rows:
        await interaction.response.send_message(
            "لا توجد إحصائيات موظفين حتى الآن.",
            ephemeral=True,
        )
        return
    sections = []
    for index, stats in enumerate(rows, start=1):
        stars = " | ".join(
            f"{star}⭐: {stats['stars'][star]}" for star in range(1, 6)
        )
        sections.append(
            f"**{index}. <@{stats['staff_id']}>**\n"
            f"المغلقة: {stats['closed']} | التقييمات: "
            f"{stats['ratings_count']} | المتوسط: "
            f"{stats['average']:.2f} | الإجمالي: "
            f"{stats['total_rating']}\n{stars}"
        )
    description = "\n\n".join(sections)
    embed = discord.Embed(
        title="إحصائيات جميع موظفي الإدارة",
        description=description[:4000],
        color=discord.Color.gold(),
        timestamp=utc_now(),
    )
    await interaction.response.send_message(embed=embed)


async def purge_slash_messages(
    interaction: discord.Interaction,
    amount: int,
) -> None:
    if (
        not isinstance(interaction.channel, discord.TextChannel)
        or not isinstance(interaction.user, discord.Member)
    ):
        await interaction.response.send_message(
            "هذا الأمر يعمل داخل القنوات النصية فقط.",
            ephemeral=True,
        )
        return
    if not interaction.user.guild_permissions.manage_messages:
        await interaction.response.send_message(
            "تحتاج إلى صلاحية Manage Messages.",
            ephemeral=True,
        )
        return
    if not 1 <= amount <= MAX_CLEAR_AMOUNT:
        await interaction.response.send_message(
            f"العدد يجب أن يكون من 1 إلى {MAX_CLEAR_AMOUNT}.",
            ephemeral=True,
        )
        return
    await interaction.response.defer(ephemeral=True)
    try:
        deleted = await interaction.channel.purge(limit=amount)
        await interaction.followup.send(
            f"تم حذف {len(deleted)} رسالة.",
            ephemeral=True,
        )
    except discord.Forbidden:
        await interaction.followup.send(
            "لا أملك صلاحية Manage Messages في هذه القناة.",
            ephemeral=True,
        )
    except discord.HTTPException as exc:
        print(f"[PURGE ERROR] {exc}", flush=True)
        await interaction.followup.send(
            "تعذر حذف الرسائل حالياً.",
            ephemeral=True,
        )


@bot.tree.command(name="clear", description="مسح عدد محدد من الرسائل")
@app_commands.describe(amount="عدد الرسائل من 1 إلى 100")
async def clear_slash(interaction: discord.Interaction, amount: int) -> None:
    await purge_slash_messages(interaction, amount)


@bot.tree.command(name="purge", description="مسح عدد محدد من الرسائل")
@app_commands.describe(amount="عدد الرسائل من 1 إلى 100")
async def purge_slash(interaction: discord.Interaction, amount: int) -> None:
    await purge_slash_messages(interaction, amount)


@setup_ticket.error
async def setup_ticket_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
) -> None:
    if isinstance(error, app_commands.errors.MissingPermissions):
        if interaction.response.is_done():
            await interaction.followup.send(
                "تحتاج إلى Administrator لاستخدام هذا الأمر.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "تحتاج إلى Administrator لاستخدام هذا الأمر.",
                ephemeral=True,
            )
        return
    print(f"[SETUP ERROR] {type(error).__name__}: {error}", flush=True)
    if interaction.response.is_done():
        await interaction.followup.send(
            "حدث خطأ غير متوقع.",
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(
            "حدث خطأ غير متوقع.",
            ephemeral=True,
        )


@bot.event
async def on_ready() -> None:
    global rating_points_recovered
    if bot.user is not None:
        leon_security_channel = bot.get_channel(1544581911102488616)
        if not isinstance(
            leon_security_channel,
            (discord.TextChannel, discord.Thread),
        ):
            print(
                "[SECURITY SETTINGS ERROR] Community LEON security log "
                "channel 1544581911102488616 is unavailable.",
                flush=True,
            )
        else:
            database.save_security_settings(
                leon_security_channel.guild.id,
                leon_security_channel.id,
            )
            print(
                f"[SECURITY] Guild {leon_security_channel.guild.id} uses "
                f"log channel {leon_security_channel.id}.",
                flush=True,
            )
        for guild in bot.guilds:
            security_settings = database.get_security_settings(guild.id)
            if security_settings is None:
                print(
                    f"[SECURITY SETTINGS] Guild {guild.id} is unconfigured; "
                    "its security events will not be routed elsewhere.",
                    flush=True,
                )
                continue
            security_channel = guild.get_channel(
                security_settings.log_channel_id
            )
            if security_channel is None:
                print(
                    f"[SECURITY SETTINGS ERROR] Guild {guild.id} cannot "
                    f"access configured channel "
                    f"{security_settings.log_channel_id}.",
                    flush=True,
                )
            else:
                print(
                    f"[SECURITY SETTINGS] Guild {guild.id} -> channel "
                    f"{security_settings.log_channel_id}.",
                    flush=True,
                )
        for guild in bot.guilds:
            settings = database.get_ticket_settings(guild.id)
            if settings is None or settings.ticket_category_id:
                continue
            try:
                category = await guild.create_category(
                    "LEON Tickets",
                    reason="Create an independent ticket category for this guild",
                )
                database.save_ticket_settings(
                    guild.id,
                    category.id,
                    settings.ticket_log_channel_id,
                    settings.support_role_id,
                )
                print(
                    f"[SETTINGS] Created ticket category {category.id} "
                    f"for guild {guild.id}.",
                    flush=True,
                )
            except discord.HTTPException as exc:
                print(
                    f"[SETTINGS ERROR] Could not create ticket category "
                    f"for guild {guild.id}: {exc}",
                    flush=True,
                )
        if bot.get_guild(CATALOG_GUILD_ID) is None:
            print(
                f"[SETTINGS] Catalog guild {CATALOG_GUILD_ID} is not "
                "available to this bot account.",
                flush=True,
            )
        print(f"[READY] Logged in as {bot.user} ({bot.user.id}).", flush=True)
        if not bot.guild_commands_synced:
            for guild in bot.guilds:
                try:
                    guild_commands = await bot.tree.sync(
                        guild=discord.Object(id=guild.id)
                    )
                    print(
                        f"[READY] Synced {len(guild_commands)} guild command(s) "
                        f"for {guild.id}; stale guild commands removed.",
                        flush=True,
                    )
                except discord.HTTPException as exc:
                    print(
                        f"[GUILD SYNC ERROR] guild={guild.id} {exc}",
                        flush=True,
                    )
            bot.guild_commands_synced = True
        if not rating_points_recovered:
            await recover_rating_points()
            await recover_pending_reports(bot)
            rating_points_recovered = True
        await bot.change_presence(
            activity=discord.Streaming(name="LEON Stream!", url=KICK_URL)
        )


def is_stream_question(content: str) -> bool:
    normalized_content = re.sub(
        r"[^\w\u0600-\u06ff]+",
        " ",
        content.casefold(),
    )
    normalized_content = " ".join(normalized_content.split())
    return any(keyword in normalized_content for keyword in STREAM_KEYWORDS)


@bot.event
async def on_message(message: discord.Message) -> None:
    """Single event pipeline so commands and custom handlers never overwrite."""
    print(
        f"[MESSAGE] author={message.author} channel={message.channel} "
        f"content={message.content!r}",
        flush=True,
    )

    if message.author == bot.user:
        return

    if message.guild is not None:
        try:
            record = database.get_ticket(message.channel.id)
            if (
                record is not None
                and not record["closed"]
                and record["owner_id"] == message.author.id
            ):
                database.update_last_member_message(
                    message.channel.id,
                    message.created_at,
                )
        except sqlite3.Error as exc:
            print(
                f"[MESSAGE DATABASE ERROR] {type(exc).__name__}: {exc}",
                flush=True,
            )

    if (
        message.guild is not None
        and not message.content.lstrip().startswith("!")
        and is_unprefixed_clear_request(message.content)
    ):
        amount = parse_clear_amount(message.content)
        if amount is None:
            await send_clear_error(message)
        else:
            await clear_messages(message, amount)
        return

    if message.content.lstrip().startswith("!"):
        await bot.process_commands(message)
        return

    if bot.user is not None and bot.user in message.mentions:
        if (
            message.guild is None
            or not isinstance(message.author, discord.Member)
            or not member_has_ai_role(message.author)
        ):
            await message.reply(
                "عذراً، هذه الميزة مخصصة لأصحاب رتبة خاصة فقط!"
            )
        else:
            await send_gemini_response(
                message,
                remove_bot_mention(message.content, bot.user),
            )
        return

    if is_stream_question(message.content):
        await message.channel.send(
            f"🎥 **بث LEON**\n"
            f"🔴 تقدر تتابع البث من هنا يا {message.author.mention}:\n{KICK_URL}"
        )

    await bot.process_commands(message)


@bot.event
async def on_command_error(
    ctx: commands.Context[LeonBot],
    error: commands.CommandError,
) -> None:
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.BadArgument):
        if ctx.command is not None and ctx.command.name == "مسح":
            await send_clear_error(ctx.message)
        return
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send(
            f"انتظر {error.retry_after:.1f} ثانية قبل استخدام الأمر مرة أخرى.",
            delete_after=5,
        )
        return
    print(f"[COMMAND ERROR] {type(error).__name__}: {error}", flush=True)


@tasks.loop(minutes=10)
async def inactivity_checker() -> None:
    """Close tickets based only on the owner's last message."""
    now = utc_now()
    for record in database.open_tickets():
        try:
            last_activity = parse_datetime(
                record["last_member_message"]
            ) or parse_datetime(record["created_at"])
            if last_activity is None or now - last_activity < timedelta(
                hours=INACTIVITY_HOURS
            ):
                continue

            channel = bot.get_channel(record["channel_id"])
            if isinstance(channel, discord.TextChannel):
                try:
                    await channel.send(
                        "تم إغلاق التذكرة تلقائياً بسبب عدم رد العضو لمدة "
                        f"{INACTIVITY_HOURS} ساعة."
                    )
                except discord.HTTPException:
                    pass
                await close_ticket(
                    channel,
                    reason=(
                        "إغلاق تلقائي بسبب عدم رد العضو لمدة "
                        f"{INACTIVITY_HOURS} ساعة"
                    ),
                    closed_by="LEON Bot",
                    auto_closed=True,
                )
        except Exception as exc:
            print(
                f"[INACTIVITY ERROR] channel={record['channel_id']} "
                f"error={type(exc).__name__}: {exc}",
                flush=True,
            )


@inactivity_checker.before_loop
async def before_inactivity_checker() -> None:
    await bot.wait_until_ready()


def main() -> None:
    """Start the bot using the required Discord secret."""
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise RuntimeError(
            "DISCORD_TOKEN is not configured. Add it as a Replit Secret."
        )
    keep_alive()
    try:
        bot.run(token)
    except discord.LoginFailure as exc:
        raise RuntimeError(
            "Discord rejected DISCORD_TOKEN. Copy a fresh bot token from "
            "Discord Developer Portal and update the Replit Secret."
        ) from exc


if __name__ == "__main__":
    main()
