from __future__ import annotations

import re
from datetime import date, datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord

from bot.models import ParticipantTarget

DATE_FORMAT = "%Y-%m-%d"
TIME_FORMAT = "%H:%M"
USER_MENTION_RE = re.compile(r"<@!?(\d+)>")
ROLE_MENTION_RE = re.compile(r"<@&(\d+)>")

WEEKDAY_NAME_TO_INDEX = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "wed": 2,
    "wednesday": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}

WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


class ParsingError(ValueError):
    """Raised when command input cannot be parsed."""


def parse_date_input(value: str) -> date:
    try:
        return datetime.strptime(value.strip(), DATE_FORMAT).date()
    except ValueError as exc:
        raise ParsingError("Date must use the format `YYYY-MM-DD`.") from exc


def parse_time_input(value: str) -> time:
    try:
        return datetime.strptime(value.strip(), TIME_FORMAT).time()
    except ValueError as exc:
        raise ParsingError("Time must use 24-hour format `HH:MM`.") from exc


def parse_timezone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value.strip())
    except ZoneInfoNotFoundError as exc:
        raise ParsingError(
            "Timezone is invalid. Use an IANA timezone such as `UTC`, "
            "`America/New_York`, or `Europe/London`."
        ) from exc


def parse_weekday_spec(value: str) -> list[int]:
    normalized = value.strip().lower().replace(" ", "")
    if not normalized:
        raise ParsingError("Weekdays cannot be empty.")

    selected: set[int] = set()
    for chunk in normalized.split(","):
        if not chunk:
            continue
        if "-" in chunk:
            start_name, end_name = chunk.split("-", maxsplit=1)
            start = _weekday_name_to_index(start_name)
            end = _weekday_name_to_index(end_name)
            if start <= end:
                selected.update(range(start, end + 1))
            else:
                selected.update(range(start, 7))
                selected.update(range(0, end + 1))
            continue

        selected.add(_weekday_name_to_index(chunk))

    if not selected:
        raise ParsingError(
            "Weekdays are invalid. Example: `mon-fri` or `mon,wed,fri`."
        )
    return sorted(selected)


def format_weekdays(weekdays: list[int]) -> str:
    if not weekdays:
        return "Not configured"
    return ", ".join(WEEKDAY_LABELS[index] for index in weekdays)


async def parse_participant_mentions(
    guild: discord.Guild, participant_input: str
) -> list[ParticipantTarget]:
    targets: dict[tuple[str, int], ParticipantTarget] = {}

    for match in ROLE_MENTION_RE.finditer(participant_input):
        role_id = int(match.group(1))
        role = guild.get_role(role_id)
        if role is None:
            raise ParsingError(f"Role mention `<@&{role_id}>` is not valid in this server.")
        targets[("role", role_id)] = ParticipantTarget(
            target_type="role",
            target_id=role.id,
            display_name=role.name,
        )

    for match in USER_MENTION_RE.finditer(participant_input):
        user_id = int(match.group(1))
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except discord.NotFound as exc:
                raise ParsingError(
                    f"User mention `<@{user_id}>` is not valid in this server."
                ) from exc
        targets[("user", user_id)] = ParticipantTarget(
            target_type="user",
            target_id=member.id,
            display_name=member.display_name,
        )

    if not targets:
        raise ParsingError(
            "Participants must include at least one user or role mention."
        )

    return list(targets.values())


def _weekday_name_to_index(name: str) -> int:
    if name not in WEEKDAY_NAME_TO_INDEX:
        raise ParsingError(
            f"Unknown weekday `{name}`. Use names like `mon`, `tuesday`, or ranges like `mon-fri`."
        )
    return WEEKDAY_NAME_TO_INDEX[name]
