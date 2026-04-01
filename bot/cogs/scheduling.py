from __future__ import annotations

from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import Database
from bot.models import GuildConfig, Meeting
from bot.parsing import (
    ParsingError,
    format_weekdays,
    parse_date_input,
    parse_participant_mentions,
    parse_time_input,
    parse_timezone,
    parse_weekday_spec,
)


class SchedulingCog(commands.Cog):
    def __init__(self, bot: commands.Bot, database: Database) -> None:
        self.bot = bot
        self.database = database

    @app_commands.command(
        name="set_admin_role",
        description="Set the role allowed to configure meeting settings.",
    )
    @app_commands.guild_only()
    async def set_admin_role(
        self, interaction: discord.Interaction, role: discord.Role
    ) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return

        config = self.database.get_guild_config(interaction.guild.id)
        if not self._is_admin(interaction.user, config):
            await interaction.response.send_message(
                "You need administrator privileges or the configured admin role to change settings.",
                ephemeral=True,
            )
            return

        config.admin_role_id = role.id
        self.database.upsert_guild_config(config)
        await interaction.response.send_message(
            f"Admin role set to {role.mention}.",
            allowed_mentions=discord.AllowedMentions(roles=False),
            ephemeral=True,
        )

    @app_commands.command(
        name="set_scheduler_role",
        description="Set the role required to schedule meetings.",
    )
    @app_commands.guild_only()
    async def set_scheduler_role(
        self, interaction: discord.Interaction, role: discord.Role
    ) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return

        config = self.database.get_guild_config(interaction.guild.id)
        if not self._is_admin(interaction.user, config):
            await interaction.response.send_message(
                "You need administrator privileges or the configured admin role to change settings.",
                ephemeral=True,
            )
            return

        config.scheduler_role_id = role.id
        self.database.upsert_guild_config(config)
        await interaction.response.send_message(
            f"Scheduler role set to {role.mention}.",
            allowed_mentions=discord.AllowedMentions(roles=False),
            ephemeral=True,
        )

    @app_commands.command(
        name="set_time_range",
        description="Set allowed weekdays, hours, and timezone for meetings.",
    )
    @app_commands.describe(
        weekdays="Example: mon-fri or mon,wed,fri",
        start_time="24-hour time, example 09:00",
        end_time="24-hour time, example 17:00",
        timezone="IANA timezone, example UTC or America/New_York",
    )
    @app_commands.guild_only()
    async def set_time_range(
        self,
        interaction: discord.Interaction,
        weekdays: str,
        start_time: str,
        end_time: str,
        timezone: str,
    ) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return

        config = self.database.get_guild_config(interaction.guild.id)
        if not self._is_admin(interaction.user, config):
            await interaction.response.send_message(
                "You need administrator privileges or the configured admin role to change settings.",
                ephemeral=True,
            )
            return

        try:
            parsed_start = parse_time_input(start_time)
            parsed_end = parse_time_input(end_time)
            parsed_timezone = parse_timezone(timezone)
            parsed_weekdays = parse_weekday_spec(weekdays)
        except ParsingError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        if parsed_start >= parsed_end:
            await interaction.response.send_message(
                "The start time must be earlier than the end time.",
                ephemeral=True,
            )
            return

        config.allowed_weekdays = parsed_weekdays
        config.start_time = parsed_start.strftime("%H:%M")
        config.end_time = parsed_end.strftime("%H:%M")
        config.timezone = str(parsed_timezone)
        self.database.upsert_guild_config(config)

        await interaction.response.send_message(
            "Allowed meeting window updated:\n"
            f"Weekdays: `{format_weekdays(config.allowed_weekdays)}`\n"
            f"Hours: `{config.start_time}`-`{config.end_time}`\n"
            f"Timezone: `{config.timezone}`",
            ephemeral=True,
        )

    @app_commands.command(
        name="show_config",
        description="Show the current scheduler role and allowed meeting window.",
    )
    @app_commands.guild_only()
    async def show_config(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return

        config = self.database.get_guild_config(interaction.guild.id)
        admin_role = (
            interaction.guild.get_role(config.admin_role_id)
            if config.admin_role_id is not None
            else None
        )
        scheduler_role = (
            interaction.guild.get_role(config.scheduler_role_id)
            if config.scheduler_role_id is not None
            else None
        )
        await interaction.response.send_message(
            "Current configuration:\n"
            f"Admin role: {admin_role.mention if admin_role else '`Not set`'}\n"
            f"Scheduler role: {scheduler_role.mention if scheduler_role else '`Not set`'}\n"
            f"Weekdays: `{format_weekdays(config.allowed_weekdays)}`\n"
            f"Hours: `{config.start_time or 'Not configured'}`-`{config.end_time or 'Not configured'}`\n"
            f"Timezone: `{config.timezone}`",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(
        name="schedule",
        description="Create a meeting and queue a reminder for participants.",
    )
    @app_commands.describe(
        date="Meeting date in YYYY-MM-DD",
        time="Meeting time in HH:MM, based on the configured timezone",
        target_channel="Text or voice channel for meeting reminders",
        title="Short meeting title",
        participants="Mention users and/or roles separated by spaces",
        details="Optional meeting details",
    )
    @app_commands.guild_only()
    async def schedule(
        self,
        interaction: discord.Interaction,
        date: str,
        time: str,
        target_channel: app_commands.AppCommandChannel,
        title: str,
        participants: str,
        details: str | None = None,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "Only server members can schedule meetings.", ephemeral=True
            )
            return

        config = self.database.get_guild_config(interaction.guild.id)
        if not self._can_schedule(interaction.user, config):
            await interaction.response.send_message(
                "You do not have permission to schedule meetings. Ask an admin to assign the scheduler role.",
                ephemeral=True,
            )
            return

        if not config.allowed_weekdays or not config.start_time or not config.end_time:
            await interaction.response.send_message(
                "Allowed meeting hours are not configured yet. Ask an admin to run `/set_time_range`.",
                ephemeral=True,
            )
            return

        meeting_channel = self._validate_target_channel(target_channel)
        if meeting_channel is None:
            await interaction.response.send_message(
                "Target channel must be a text channel or voice channel.",
                ephemeral=True,
            )
            return

        try:
            meeting_date = parse_date_input(date)
            meeting_time = parse_time_input(time)
            participant_targets = await parse_participant_mentions(
                interaction.guild, participants
            )
        except ParsingError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        guild_timezone = parse_timezone(config.timezone)
        local_start = datetime.combine(meeting_date, meeting_time).replace(
            tzinfo=guild_timezone
        )
        now_local = datetime.now(guild_timezone)

        if local_start <= now_local:
            await interaction.response.send_message(
                "The meeting time must be in the future.",
                ephemeral=True,
            )
            return

        if not self._is_within_allowed_window(local_start, config):
            await interaction.response.send_message(
                "That meeting time is outside the allowed meeting window.\n"
                f"Allowed weekdays: `{format_weekdays(config.allowed_weekdays)}`\n"
                f"Allowed hours: `{config.start_time}`-`{config.end_time}` `{config.timezone}`",
                ephemeral=True,
            )
            return

        starts_at_utc = local_start.astimezone(timezone.utc)

        meeting_id = self.database.create_meeting(
            guild_id=interaction.guild.id,
            channel_id=meeting_channel.id,
            creator_id=interaction.user.id,
            title=title,
            details=details,
            starts_at_utc=starts_at_utc,
            participant_targets=participant_targets,
        )
        mention_preview = " ".join(target.mention for target in participant_targets)
        start_unix = int(starts_at_utc.timestamp())

        await interaction.response.send_message(
            "Meeting scheduled.\n"
            f"ID: `{meeting_id}`\n"
            f"Title: **{discord.utils.escape_markdown(title)}**\n"
            f"When: <t:{start_unix}:F>\n"
            f"Reminder channel: {meeting_channel.mention}\n"
            "DM notifications: `24h before`, `1h before`, and `at start`\n"
            f"Participants: {mention_preview}\n"
            f"Organizer DM: <@{interaction.user.id}>"
            + (f"\nDetails: {details}" if details else ""),
            allowed_mentions=discord.AllowedMentions(users=False, roles=False),
        )

    @app_commands.command(
        name="edit_meeting",
        description="Reschedule or update a scheduled meeting.",
    )
    @app_commands.describe(
        meeting="Select the meeting to edit",
        date="New date in YYYY-MM-DD",
        time="New time in HH:MM, based on the configured timezone",
        target_channel="New text or voice channel for meeting reminders",
        title="New title",
        participants="New user and/or role mentions separated by spaces",
        details="New details text",
    )
    @app_commands.guild_only()
    async def edit_meeting(
        self,
        interaction: discord.Interaction,
        meeting: str,
        date: str | None = None,
        time: str | None = None,
        target_channel: app_commands.AppCommandChannel | None = None,
        title: str | None = None,
        participants: str | None = None,
        details: str | None = None,
    ) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "This command can only be used in a server.",
                ephemeral=True,
            )
            return

        meeting_id = self._parse_meeting_choice(meeting)
        if meeting_id is None:
            await interaction.response.send_message(
                "Invalid meeting selection.",
                ephemeral=True,
            )
            return

        existing_meeting = self.database.get_meeting(meeting_id, interaction.guild.id)
        if existing_meeting is None or existing_meeting.status != "scheduled":
            await interaction.response.send_message(
                "Meeting not found, or it has already been cancelled.",
                ephemeral=True,
            )
            return

        config = self.database.get_guild_config(interaction.guild.id)
        is_creator = existing_meeting.creator_id == interaction.user.id
        if not (self._can_schedule(interaction.user, config) or is_creator):
            await interaction.response.send_message(
                "You can only edit meetings you created unless you are an admin or scheduler.",
                ephemeral=True,
            )
            return

        if (
            date is None
            and time is None
            and target_channel is None
            and title is None
            and participants is None
            and details is None
        ):
            await interaction.response.send_message(
                "Provide at least one field to update.",
                ephemeral=True,
            )
            return

        guild_timezone = parse_timezone(config.timezone)
        existing_local_start = existing_meeting.starts_at_utc.astimezone(guild_timezone)

        try:
            new_date = parse_date_input(date) if date is not None else existing_local_start.date()
            new_time = parse_time_input(time) if time is not None else existing_local_start.time().replace(
                tzinfo=None, second=0, microsecond=0
            )
            participant_targets = (
                await parse_participant_mentions(interaction.guild, participants)
                if participants is not None
                else existing_meeting.participant_targets
            )
        except ParsingError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        if target_channel is not None:
            meeting_channel = self._validate_target_channel(target_channel)
        else:
            meeting_channel = None

        if target_channel is not None and meeting_channel is None:
            await interaction.response.send_message(
                "Target channel must be a text channel or voice channel.",
                ephemeral=True,
            )
            return

        new_local_start = datetime.combine(new_date, new_time).replace(
            tzinfo=guild_timezone
        )
        now_local = datetime.now(guild_timezone)
        if new_local_start <= now_local:
            await interaction.response.send_message(
                "The meeting time must be in the future.",
                ephemeral=True,
            )
            return

        start_changed = date is not None or time is not None
        if start_changed and not self._is_within_allowed_window(new_local_start, config):
            await interaction.response.send_message(
                "That meeting time is outside the allowed meeting window.\n"
                f"Allowed weekdays: `{format_weekdays(config.allowed_weekdays)}`\n"
                f"Allowed hours: `{config.start_time}`-`{config.end_time}` `{config.timezone}`",
                ephemeral=True,
            )
            return

        updated_title = title if title is not None else existing_meeting.title
        updated_details = details if details is not None else existing_meeting.details
        updated_starts_at_utc = new_local_start.astimezone(timezone.utc)
        reset_notifications = updated_starts_at_utc != existing_meeting.starts_at_utc

        updated = self.database.update_meeting(
            meeting_id=existing_meeting.meeting_id,
            guild_id=interaction.guild.id,
            title=updated_title,
            details=updated_details,
            starts_at_utc=updated_starts_at_utc,
            channel_id=meeting_channel.id if meeting_channel is not None else existing_meeting.channel_id,
            participant_targets=participant_targets,
            reset_notifications=reset_notifications,
        )
        if not updated:
            await interaction.response.send_message(
                "Meeting could not be updated.",
                ephemeral=True,
            )
            return

        start_unix = int(updated_starts_at_utc.timestamp())
        mention_preview = " ".join(target.mention for target in participant_targets)
        await interaction.response.send_message(
            "Meeting updated.\n"
            f"ID: `{existing_meeting.meeting_id}`\n"
            f"Title: **{discord.utils.escape_markdown(updated_title)}**\n"
            f"When: <t:{start_unix}:F>\n"
            f"Reminder channel: <#{meeting_channel.id if meeting_channel is not None else existing_meeting.channel_id}>\n"
            f"Participants: {mention_preview}"
            + (f"\nDetails: {updated_details}" if updated_details else ""),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(
        name="list_meetings",
        description="List scheduled meetings for this server.",
    )
    @app_commands.guild_only()
    async def list_meetings(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return

        meetings = self.database.list_upcoming_meetings(interaction.guild.id)
        future_meetings = [
            meeting
            for meeting in meetings
            if meeting.starts_at_utc > datetime.now(timezone.utc)
        ]
        if not future_meetings:
            await interaction.response.send_message(
                "There are no upcoming scheduled meetings.", ephemeral=True
            )
            return

        lines = []
        for meeting in future_meetings[:20]:
            lines.append(self._format_meeting_line(meeting))
        await interaction.response.send_message(
            "Upcoming meetings:\n" + "\n".join(lines),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(
        name="my_meetings",
        description="List upcoming meetings you are invited to in this server.",
    )
    @app_commands.guild_only()
    async def my_meetings(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return

        meetings = self.database.list_upcoming_meetings(interaction.guild.id)
        now_utc = datetime.now(timezone.utc)
        member_role_ids = {role.id for role in interaction.user.roles}

        invited = []
        for meeting in meetings:
            if meeting.starts_at_utc <= now_utc:
                continue
            for target in meeting.participant_targets:
                if target.target_type == "user" and target.target_id == interaction.user.id:
                    invited.append(meeting)
                    break
                if target.target_type == "role" and target.target_id in member_role_ids:
                    invited.append(meeting)
                    break

        if not invited:
            await interaction.response.send_message(
                "You are not invited to any upcoming meetings in this server.",
                ephemeral=True,
            )
            return

        lines = [self._format_meeting_line(meeting) for meeting in invited[:20]]
        await interaction.response.send_message(
            "Upcoming meetings you're invited to:\n" + "\n".join(lines),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(
        name="cancel_meeting",
        description="Cancel a scheduled meeting by its ID.",
    )
    @app_commands.guild_only()
    async def cancel_meeting(
        self, interaction: discord.Interaction, meeting: str
    ) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return

        meeting_id = self._parse_meeting_choice(meeting)
        if meeting_id is None:
            await interaction.response.send_message(
                "Invalid meeting selection.",
                ephemeral=True,
            )
            return

        meeting = self.database.get_meeting(meeting_id, interaction.guild.id)
        if meeting is None or meeting.status != "scheduled":
            await interaction.response.send_message(
                "Meeting not found, or it has already been cancelled.",
                ephemeral=True,
            )
            return

        config = self.database.get_guild_config(interaction.guild.id)
        is_creator = meeting.creator_id == interaction.user.id
        if not (self._can_schedule(interaction.user, config) or is_creator):
            await interaction.response.send_message(
                "You can only cancel meetings you created unless you are an admin or scheduler.",
                ephemeral=True,
            )
            return

        cancelled = self.database.cancel_meeting(meeting_id, interaction.guild.id)
        if not cancelled:
            await interaction.response.send_message(
                "Meeting not found, or it has already been cancelled.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"Meeting `{meeting_id}` has been cancelled.",
            ephemeral=True,
        )

    @cancel_meeting.autocomplete("meeting")
    @edit_meeting.autocomplete("meeting")
    async def meeting_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return []

        config = self.database.get_guild_config(interaction.guild.id)
        creator_filter = None
        if not self._can_schedule(interaction.user, config):
            creator_filter = interaction.user.id

        meetings = self.database.search_upcoming_meetings(
            guild_id=interaction.guild.id,
            query=current,
            creator_id=creator_filter,
        )
        try:
            guild_timezone = parse_timezone(config.timezone)
        except ParsingError:
            guild_timezone = timezone.utc

        choices: list[app_commands.Choice[str]] = []
        for meeting in meetings[:25]:
            local_start = meeting.starts_at_utc.astimezone(guild_timezone)
            channel = self._resolve_existing_channel(interaction.guild, meeting.channel_id)
            channel_label = channel.name if channel is not None else f"channel-{meeting.channel_id}"
            title_label = discord.utils.escape_markdown(meeting.title)[:40]
            name = (
                f"{meeting.meeting_id} | {title_label} | "
                f"{local_start:%Y-%m-%d %H:%M} | #{channel_label}"
            )[:100]
            choices.append(app_commands.Choice(name=name, value=str(meeting.meeting_id)))
        return choices

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(
                f"Command failed: {error}", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"Command failed: {error}", ephemeral=True
        )

    def _is_admin(self, member: discord.Member, config: GuildConfig) -> bool:
        if member.guild_permissions.administrator:
            return True
        if config.admin_role_id is None:
            return False
        return any(role.id == config.admin_role_id for role in member.roles)

    def _can_schedule(self, member: discord.Member, config: GuildConfig) -> bool:
        if self._is_admin(member, config):
            return True
        if config.scheduler_role_id is None:
            return False
        return any(role.id == config.scheduler_role_id for role in member.roles)

    def _is_within_allowed_window(
        self, local_start: datetime, config: GuildConfig
    ) -> bool:
        if local_start.weekday() not in config.allowed_weekdays:
            return False
        start_bound = datetime.strptime(config.start_time, "%H:%M").time()
        end_bound = datetime.strptime(config.end_time, "%H:%M").time()
        return start_bound <= local_start.timetz().replace(tzinfo=None) <= end_bound

    def _format_meeting_line(self, meeting: Meeting) -> str:
        mentions = " ".join(target.mention for target in meeting.participant_targets)
        start_unix = int(meeting.starts_at_utc.timestamp())
        return (
            f"`{meeting.meeting_id}` | **{discord.utils.escape_markdown(meeting.title)}** | "
            f"<t:{start_unix}:F> | <#{meeting.channel_id}> | DMs `24h` `1h` `start` | {mentions}"
        )

    def _parse_meeting_choice(self, value: str) -> int | None:
        try:
            return int(value)
        except ValueError:
            return None

    def _validate_target_channel(
        self, channel: app_commands.AppCommandChannel
    ) -> app_commands.AppCommandChannel | None:
        # In discord.py 2.4+, channel options may resolve to lightweight "app command"
        # channel objects rather than concrete TextChannel/VoiceChannel instances.
        # The only capability we need here is a valid channel `id`.
        if isinstance(channel, (discord.TextChannel, discord.VoiceChannel, discord.StageChannel)):
            return channel
        channel_type = getattr(channel, "type", None)
        channel_type_value = (
            getattr(channel_type, "value", None)
            if not isinstance(channel_type, int)
            else int(channel_type)
        )
        channel_id = getattr(channel, "id", None)
        # Discord API channel type values:
        # 0 = GUILD_TEXT, 2 = GUILD_VOICE, 5 = GUILD_ANNOUNCEMENT, 13 = GUILD_STAGE_VOICE
        if isinstance(channel_id, int) and channel_type_value in {0, 2, 5, 13}:
            return channel
        return None

    def _resolve_existing_channel(
        self, guild: discord.Guild, channel_id: int
    ) -> discord.TextChannel | discord.VoiceChannel | discord.StageChannel | None:
        channel = guild.get_channel(channel_id)
        if isinstance(channel, (discord.TextChannel, discord.VoiceChannel, discord.StageChannel)):
            return channel
        return None
