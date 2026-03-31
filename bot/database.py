from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bot.models import GuildConfig, Meeting, ParticipantTarget


class Database:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.db_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row

    def initialize(self) -> None:
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS guild_config (
                    guild_id INTEGER PRIMARY KEY,
                    admin_role_id INTEGER,
                    scheduler_role_id INTEGER,
                    timezone TEXT NOT NULL DEFAULT 'UTC',
                    allowed_weekdays TEXT NOT NULL DEFAULT '',
                    start_time TEXT,
                    end_time TEXT,
                    updated_at_utc TEXT NOT NULL
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS meetings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    creator_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    details TEXT,
                    starts_at_utc TEXT NOT NULL,
                    participant_targets TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'scheduled',
                    reminder_24h_sent INTEGER NOT NULL DEFAULT 0,
                    reminder_1h_sent INTEGER NOT NULL DEFAULT 0,
                    start_notification_sent INTEGER NOT NULL DEFAULT 0,
                    created_at_utc TEXT NOT NULL
                )
                """
            )
        self._migrate_meetings_table()

    def get_guild_config(self, guild_id: int) -> GuildConfig:
        row = self._connection.execute(
            "SELECT * FROM guild_config WHERE guild_id = ?",
            (guild_id,),
        ).fetchone()
        if row is None:
            return GuildConfig(
                guild_id=guild_id,
                admin_role_id=None,
                scheduler_role_id=None,
                timezone="UTC",
                allowed_weekdays=[],
                start_time=None,
                end_time=None,
            )
        return GuildConfig(
            guild_id=row["guild_id"],
            admin_role_id=row["admin_role_id"],
            scheduler_role_id=row["scheduler_role_id"],
            timezone=row["timezone"],
            allowed_weekdays=_deserialize_weekdays(row["allowed_weekdays"]),
            start_time=row["start_time"],
            end_time=row["end_time"],
        )

    def upsert_guild_config(self, config: GuildConfig) -> None:
        now_utc = _utc_now().isoformat()
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO guild_config (
                    guild_id,
                    admin_role_id,
                    scheduler_role_id,
                    timezone,
                    allowed_weekdays,
                    start_time,
                    end_time,
                    updated_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    admin_role_id = excluded.admin_role_id,
                    scheduler_role_id = excluded.scheduler_role_id,
                    timezone = excluded.timezone,
                    allowed_weekdays = excluded.allowed_weekdays,
                    start_time = excluded.start_time,
                    end_time = excluded.end_time,
                    updated_at_utc = excluded.updated_at_utc
                """,
                (
                    config.guild_id,
                    config.admin_role_id,
                    config.scheduler_role_id,
                    config.timezone,
                    _serialize_weekdays(config.allowed_weekdays),
                    config.start_time,
                    config.end_time,
                    now_utc,
                ),
            )

    def create_meeting(
        self,
        guild_id: int,
        channel_id: int,
        creator_id: int,
        title: str,
        details: str | None,
        starts_at_utc: datetime,
        participant_targets: list[ParticipantTarget],
    ) -> int:
        now_utc = _utc_now()
        reminder_24h_sent = starts_at_utc <= now_utc + timedelta(hours=24)
        reminder_1h_sent = starts_at_utc <= now_utc + timedelta(hours=1)
        with self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO meetings (
                    guild_id,
                    channel_id,
                    creator_id,
                    title,
                    details,
                    starts_at_utc,
                    participant_targets,
                    reminder_24h_sent,
                    reminder_1h_sent,
                    created_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    guild_id,
                    channel_id,
                    creator_id,
                    title,
                    details,
                    starts_at_utc.isoformat(),
                    json.dumps(
                        [
                            {
                                "target_type": target.target_type,
                                "target_id": target.target_id,
                                "display_name": target.display_name,
                            }
                            for target in participant_targets
                        ]
                    ),
                    int(reminder_24h_sent),
                    int(reminder_1h_sent),
                    now_utc.isoformat(),
                ),
            )
        return int(cursor.lastrowid)

    def get_meeting(self, meeting_id: int, guild_id: int) -> Meeting | None:
        row = self._connection.execute(
            "SELECT * FROM meetings WHERE id = ? AND guild_id = ?",
            (meeting_id, guild_id),
        ).fetchone()
        if row is None:
            return None
        return self._meeting_from_row(row)

    def list_upcoming_meetings(self, guild_id: int) -> list[Meeting]:
        rows = self._connection.execute(
            """
            SELECT * FROM meetings
            WHERE guild_id = ? AND status = 'scheduled'
            ORDER BY starts_at_utc ASC
            """,
            (guild_id,),
        ).fetchall()
        return [self._meeting_from_row(row) for row in rows]

    def list_notification_candidates(self) -> list[Meeting]:
        rows = self._connection.execute(
            """
            SELECT * FROM meetings
            WHERE status = 'scheduled'
              AND (
                reminder_24h_sent = 0
                OR reminder_1h_sent = 0
                OR start_notification_sent = 0
              )
            ORDER BY starts_at_utc ASC
            """
        ).fetchall()
        return [self._meeting_from_row(row) for row in rows]

    def mark_notification_sent(self, meeting_id: int, stage: str) -> None:
        column = _notification_stage_column(stage)
        with self._connection:
            self._connection.execute(
                f"UPDATE meetings SET {column} = 1 WHERE id = ?",
                (meeting_id,),
            )

    def cancel_meeting(self, meeting_id: int, guild_id: int) -> bool:
        with self._connection:
            cursor = self._connection.execute(
                """
                UPDATE meetings
                SET status = 'cancelled'
                WHERE id = ? AND guild_id = ? AND status = 'scheduled'
                """,
                (meeting_id, guild_id),
            )
        return cursor.rowcount > 0

    def _meeting_from_row(self, row: sqlite3.Row) -> Meeting:
        return Meeting(
            meeting_id=row["id"],
            guild_id=row["guild_id"],
            channel_id=row["channel_id"],
            creator_id=row["creator_id"],
            title=row["title"],
            details=row["details"],
            starts_at_utc=datetime.fromisoformat(row["starts_at_utc"]).astimezone(
                timezone.utc
            ),
            participant_targets=[
                ParticipantTarget(
                    target_type=item["target_type"],
                    target_id=item["target_id"],
                    display_name=item["display_name"],
                )
                for item in json.loads(row["participant_targets"])
            ],
            status=row["status"],
            reminder_24h_sent=bool(row["reminder_24h_sent"]),
            reminder_1h_sent=bool(row["reminder_1h_sent"]),
            start_notification_sent=bool(row["start_notification_sent"]),
            created_at_utc=datetime.fromisoformat(row["created_at_utc"]).astimezone(
                timezone.utc
            ),
        )

    def _migrate_meetings_table(self) -> None:
        columns = {
            row["name"] for row in self._connection.execute("PRAGMA table_info(meetings)")
        }
        required_columns = {
            "reminder_24h_sent": (
                "ALTER TABLE meetings ADD COLUMN reminder_24h_sent INTEGER NOT NULL DEFAULT 0"
            ),
            "reminder_1h_sent": (
                "ALTER TABLE meetings ADD COLUMN reminder_1h_sent INTEGER NOT NULL DEFAULT 0"
            ),
            "start_notification_sent": (
                "ALTER TABLE meetings ADD COLUMN start_notification_sent INTEGER NOT NULL DEFAULT 0"
            ),
        }
        with self._connection:
            for column, statement in required_columns.items():
                if column not in columns:
                    self._connection.execute(statement)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _serialize_weekdays(weekdays: list[int]) -> str:
    return ",".join(str(value) for value in sorted(set(weekdays)))


def _deserialize_weekdays(value: str) -> list[int]:
    if not value:
        return []
    return [int(item) for item in value.split(",") if item]


def _notification_stage_column(stage: str) -> str:
    mapping = {
        "24h": "reminder_24h_sent",
        "1h": "reminder_1h_sent",
        "start": "start_notification_sent",
    }
    if stage not in mapping:
        raise ValueError(f"Unsupported notification stage: {stage}")
    return mapping[stage]
