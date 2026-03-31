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
        title: str,
        participants: str,
        details: str | None = None,
    ) -> None:
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message(
                "This command can only be used in a server channel.",
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
            channel_id=interaction.channel.id,
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
            "DM notifications: `24h before`, `1h before`, and `at start`\n"
            f"Participants: {mention_preview}\n"
            f"Organizer DM: <@{interaction.user.id}>"
            + (f"\nDetails: {details}" if details else ""),
            allowed_mentions=discord.AllowedMentions(users=False, roles=False),
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
        name="cancel_meeting",
        description="Cancel a scheduled meeting by its ID.",
    )
    @app_commands.guild_only()
    async def cancel_meeting(
        self, interaction: discord.Interaction, meeting_id: int
    ) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
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
            f"<t:{start_unix}:F> | DMs `24h` `1h` `start` | {mentions}"
        )
