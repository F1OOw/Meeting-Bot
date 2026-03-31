from __future__ import annotations

import logging
import os
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

from bot.cogs.scheduling import SchedulingCog
from bot.database import Database
from bot.reminders import ReminderService

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
configured_data_dir = Path(os.getenv("BOT_DATA_DIR", "data"))
DATA_DIR = (
    configured_data_dir
    if configured_data_dir.is_absolute()
    else PROJECT_ROOT / configured_data_dir
)
DATABASE_PATH = DATA_DIR / "meetings.sqlite3"


class MeetingBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True

        super().__init__(command_prefix="!", intents=intents)
        self.database = Database(DATABASE_PATH)
        self.reminder_service = ReminderService(self, self.database)

    async def setup_hook(self) -> None:
        self.database.initialize()
        await self.add_cog(SchedulingCog(self, self.database))
        self.reminder_service.start()

        guild_id = os.getenv("DISCORD_GUILD_ID")
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logging.getLogger(__name__).info("Synced commands to guild %s", guild_id)
        else:
            await self.tree.sync()
            logging.getLogger(__name__).info("Synced global commands")

    async def close(self) -> None:
        await self.reminder_service.stop()
        await super().close()


def configure_logging() -> None:
    logging.basicConfig(
        level=LOG_LEVEL,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def run() -> None:
    configure_logging()
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("Set the DISCORD_BOT_TOKEN environment variable.")

    bot = MeetingBot()
    bot.run(token, log_handler=None)
