from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class GuildConfig:
    guild_id: int
    admin_role_id: int | None
    scheduler_role_id: int | None
    timezone: str
    allowed_weekdays: list[int]
    start_time: str | None
    end_time: str | None


@dataclass(slots=True)
class ParticipantTarget:
    target_type: str
    target_id: int
    display_name: str

    @property
    def mention(self) -> str:
        if self.target_type == "role":
            return f"<@&{self.target_id}>"
        return f"<@{self.target_id}>"


@dataclass(slots=True)
class Meeting:
    meeting_id: int
    guild_id: int
    channel_id: int
    creator_id: int
    title: str
    details: str | None
    starts_at_utc: datetime
    participant_targets: list[ParticipantTarget]
    status: str
    reminder_24h_sent: bool
    reminder_1h_sent: bool
    start_notification_sent: bool
    created_at_utc: datetime
