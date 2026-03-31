from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import discord

from bot.database import Database
from bot.models import Meeting

logger = logging.getLogger(__name__)


class ReminderService:
    def __init__(self, bot: discord.Client, database: Database, poll_interval: int = 30) -> None:
        self.bot = bot
        self.database = database
        self.poll_interval = poll_interval
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="meeting-reminders")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                await self._dispatch_due_reminders()
            except Exception:
                logger.exception("Reminder loop failed")
            await asyncio.sleep(self.poll_interval)

    async def _dispatch_due_reminders(self) -> None:
        now_utc = datetime.now(timezone.utc)
        meetings = self.database.list_notification_candidates()
        for meeting in meetings:
            await self._process_meeting_notifications(meeting, now_utc)

    async def _process_meeting_notifications(
        self, meeting: Meeting, now_utc: datetime
    ) -> None:
        start_at = meeting.starts_at_utc
        one_hour_before = start_at - timedelta(hours=1)
        twenty_four_hours_before = start_at - timedelta(hours=24)

        if now_utc >= start_at:
            if not meeting.reminder_24h_sent:
                self.database.mark_notification_sent(meeting.meeting_id, "24h")
            if not meeting.reminder_1h_sent:
                self.database.mark_notification_sent(meeting.meeting_id, "1h")
            if not meeting.start_notification_sent:
                await self._send_notification(meeting, "start")
            return

        if now_utc >= one_hour_before:
            if not meeting.reminder_24h_sent:
                self.database.mark_notification_sent(meeting.meeting_id, "24h")
            if not meeting.reminder_1h_sent:
                await self._send_notification(meeting, "1h")
            return

        if now_utc >= twenty_four_hours_before and not meeting.reminder_24h_sent:
            await self._send_notification(meeting, "24h")

    async def _send_notification(self, meeting: Meeting, stage: str) -> None:
        await self._send_channel_notification(meeting, stage)

        recipients = await self._resolve_dm_recipients(meeting)
        if not recipients:
            logger.warning(
                "Skipping %s notification for meeting %s because no DM recipients were resolved",
                stage,
                meeting.meeting_id,
            )
            self.database.mark_notification_sent(meeting.meeting_id, stage)
            return

        start_unix = int(meeting.starts_at_utc.timestamp())
        channel_text = f"<#{meeting.channel_id}>"
        details_text = f"\nDetails: {meeting.details}" if meeting.details else ""
        stage_text = _stage_text(stage)
        message = (
            f"{stage_text}\n"
            f"Meeting: **{meeting.title}**\n"
            f"When: <t:{start_unix}:F> (<t:{start_unix}:R>)\n"
            f"Channel: {channel_text}\n"
            f"Meeting ID: `{meeting.meeting_id}`{details_text}"
        )

        for recipient in recipients:
            try:
                await recipient.send(message)
            except (discord.Forbidden, discord.NotFound):
                logger.warning(
                    "Could not DM user %s for %s notification of meeting %s",
                    recipient.id,
                    stage,
                    meeting.meeting_id,
                )
            except discord.HTTPException:
                logger.exception(
                    "Discord API error while DMing user %s for meeting %s",
                    recipient.id,
                    meeting.meeting_id,
                )

        self.database.mark_notification_sent(meeting.meeting_id, stage)

    async def _send_channel_notification(self, meeting: Meeting, stage: str) -> None:
        channel = self.bot.get_channel(meeting.channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(meeting.channel_id)
            except (discord.NotFound, discord.Forbidden):
                logger.warning(
                    "Could not access target channel %s for meeting %s",
                    meeting.channel_id,
                    meeting.meeting_id,
                )
                return

        if not hasattr(channel, "send"):
            logger.warning(
                "Target channel %s for meeting %s does not support messages",
                meeting.channel_id,
                meeting.meeting_id,
            )
            return

        start_unix = int(meeting.starts_at_utc.timestamp())
        details_text = f"\nDetails: {meeting.details}" if meeting.details else ""
        mentions = self._channel_mentions(meeting)
        message = (
            f"{mentions}\n{_stage_text(stage)}\n"
            f"Meeting: **{meeting.title}**\n"
            f"When: <t:{start_unix}:F> (<t:{start_unix}:R>)\n"
            f"Meeting ID: `{meeting.meeting_id}`{details_text}"
        )

        try:
            await channel.send(
                message,
                allowed_mentions=discord.AllowedMentions(users=True, roles=True),
            )
        except (discord.Forbidden, discord.NotFound):
            logger.warning(
                "Failed to send channel notification for meeting %s to channel %s",
                meeting.meeting_id,
                meeting.channel_id,
            )
        except discord.HTTPException:
            logger.exception(
                "Discord API error while sending channel notification for meeting %s",
                meeting.meeting_id,
            )

    async def _resolve_dm_recipients(self, meeting: Meeting) -> list[discord.abc.User]:
        recipients: dict[int, discord.abc.User] = {}

        creator = self.bot.get_user(meeting.creator_id)
        if creator is None:
            try:
                creator = await self.bot.fetch_user(meeting.creator_id)
            except discord.NotFound:
                creator = None
        if creator is not None:
            recipients[creator.id] = creator

        guild = self.bot.get_guild(meeting.guild_id)
        if guild is None:
            logger.warning(
                "Skipping guild-specific recipients for meeting %s because guild %s is not cached",
                meeting.meeting_id,
                meeting.guild_id,
            )
            return list(recipients.values())

        if not guild.chunked:
            try:
                await guild.chunk(cache=True)
            except discord.HTTPException:
                logger.exception("Failed to chunk guild %s before sending reminders", guild.id)

        for target in meeting.participant_targets:
            if target.target_type == "user":
                member = guild.get_member(target.target_id)
                if member is None:
                    try:
                        member = await guild.fetch_member(target.target_id)
                    except discord.NotFound:
                        logger.warning(
                            "User target %s for meeting %s no longer exists in guild %s",
                            target.target_id,
                            meeting.meeting_id,
                            guild.id,
                        )
                        continue
                recipients[member.id] = member
                continue

            role = guild.get_role(target.target_id)
            if role is None:
                logger.warning(
                    "Role target %s for meeting %s no longer exists in guild %s",
                    target.target_id,
                    meeting.meeting_id,
                    guild.id,
                )
                continue
            for member in role.members:
                recipients[member.id] = member

        return list(recipients.values())

    def _channel_mentions(self, meeting: Meeting) -> str:
        mentions = {target.mention for target in meeting.participant_targets}
        mentions.add(f"<@{meeting.creator_id}>")
        return " ".join(sorted(mentions))


def _stage_text(stage: str) -> str:
    mapping = {
        "24h": "Reminder: your meeting starts in about 24 hours.",
        "1h": "Reminder: your meeting starts in about 1 hour.",
        "start": "Reminder: your meeting is starting now.",
    }
    return mapping[stage]
