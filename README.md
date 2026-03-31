# Discord Meeting Scheduler Bot

A `discord.py` slash-command bot that lets authorized users schedule meetings, enforce admin-defined meeting windows, and send automatic reminders to tagged users or roles.

## Features

- Server admins or a designated admin role can configure:
  - allowed weekdays
  - allowed meeting hours
  - server timezone
  - scheduler role
- Only members with the scheduler role can create meetings.
- Meeting validation rejects:
  - invalid date and time formats
  - past dates
  - meetings outside the allowed meeting window
- Persistent SQLite storage for:
  - guild configuration
  - scheduled meetings
  - reminder state
- Background reminder worker that survives bot restarts because meetings are stored in SQLite.
- Automatic DM notifications sent:
  - 24 hours before the meeting
  - 1 hour before the meeting
  - when the meeting starts
- DM recipients include:
  - all tagged users
  - all current members of tagged roles
  - the meeting creator

## Project Structure

```text
main.py
bot/
  app.py
  database.py
  models.py
  parsing.py
  reminders.py
  cogs/
    scheduling.py
Dockerfile
docker-compose.yml
requirements.txt
.env.example
```

## Setup

1. Create a Discord application in the Discord Developer Portal.
2. Add a bot user to that application.
3. Under `Bot`, enable these privileged intents:
   - `Server Members Intent`
4. Invite the bot to your server with scopes:
   - `bot`
   - `applications.commands`
5. Ensure the bot has permissions to:
   - view channels
   - send messages
   - mention roles
6. Create a Python 3.10+ virtual environment and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

7. Create a `.env` file in the project root:

```bash
cp .env.example .env
```

Then update `.env`:

```env
DISCORD_BOT_TOKEN=your-bot-token
DISCORD_GUILD_ID=
BOT_DATA_DIR=./data
LOG_LEVEL=INFO
```

8. Start the bot:

```bash
python main.py
```

The app loads `.env` automatically on startup.

If `DISCORD_GUILD_ID` is set, slash commands sync to that guild immediately. Without it, commands sync globally and may take longer to appear.

## Docker

1. Create `.env` from the example and fill in the bot token.
2. Start the container:

```bash
docker compose up --build -d
```

3. Stop it:

```bash
docker compose down
```

Notes:

- The SQLite database is stored in `./data` on the host.
- The default `BOT_DATA_DIR=./data` works locally and in Docker.

## Slash Commands

- `/set_admin_role role`
  - Sets the role allowed to configure bot settings.
  - Native Discord administrators are always treated as admins.
- `/set_scheduler_role role`
  - Sets the role required to schedule meetings.
- `/set_time_range weekdays start_time end_time timezone`
  - Example:
    - `weekdays`: `mon-fri`
    - `start_time`: `09:00`
    - `end_time`: `17:00`
    - `timezone`: `America/New_York`
- `/show_config`
  - Shows the active configuration for the server.
- `/schedule date time title participants details`
  - Example values:
    - `date`: `2026-04-02`
    - `time`: `14:30`
    - `title`: `Sprint Planning`
    - `participants`: `<@123...> <@&456...>`
    - `details`: `Bring backlog updates`
- `/list_meetings`
  - Lists upcoming meetings.
- `/cancel_meeting meeting_id`
  - Cancels a scheduled meeting.

## Command Usage Notes

- Meeting times are interpreted in the configured guild timezone.
- `participants` must include user mentions like `<@123...>` or role mentions like `<@&456...>`.
- Administrators bypass the scheduler role restriction.
- DM reminders go to tagged users, current members of tagged roles, and the meeting creator.
- Each DM includes the meeting title, time, channel, and optional details.

## Error Handling

The bot explicitly handles these cases:

- invalid `YYYY-MM-DD` date format
- invalid `HH:MM` 24-hour time format
- invalid timezone names
- invalid weekday expressions
- missing scheduler permission
- meeting time in the past
- meeting time outside the configured meeting window
- invalid or missing user/role mentions
- attempts to cancel missing or already-cancelled meetings

## Storage

- SQLite database file: `data/meetings.sqlite3` by default
- Tables:
  - `guild_config`
  - `meetings`
