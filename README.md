# Personal Discord bot

A Python bot for one personal Discord server: automatic member roles, welcome/goodbye messages, and help/ping commands.

- `main` contains the core bot for future development.
- `music` preserves YouTube playback, playlists, voice controls, and their Docker dependencies.

1. [Run the container](#part-1-run-the-container)
2. [Set up the bot on Discord](#part-2-set-up-the-bot-on-discord)

**First-time setup:** prepare Docker and `.env` in Part 1, complete Part 2 to get your token and server IDs, then start the container.

## Part 1: Run the container

### Step 1: Check prerequisites

Install Docker Desktop and start its Linux container engine. Use Docker Compose 2.30 or later for raw environment files. Keep your computer awake and connected while hosting locally.

In PowerShell:

```powershell
Set-Location C:\Users\risto\projects\discord-bot
docker version
docker compose version
```

`docker version` must show both Client and Server information. If the Server section fails, wait for Docker Desktop to finish starting.

The image includes Python 3.12 and installs `requirements.txt` during the build. The core bot needs `discord.py`; pip installs its networking dependencies automatically. Music tools and voice encryption packages are only required on the `music` branch.

### Step 2: Configure the environment

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

| Setting | Required? | Value |
| --- | --- | --- |
| `DISCORD_TOKEN` | Yes | Your application's bot token |
| `GUILD_ID` | Yes | Your server's numeric ID |
| `DEFAULT_ROLE_IDS` | Yes | Numeric role IDs separated by commas |
| `WELCOME_CHANNEL_ID` | Yes | Welcome text-channel ID |
| `GOODBYE_CHANNEL_ID` | Yes | Goodbye text-channel ID |
| `COMMAND_PREFIX` | No | Defaults to `~`; change it if another bot uses the same prefix |
| `WELCOME_MESSAGE` | No | Defaults to `Welcome {mention} to {server}!` |
| `GOODBYE_MESSAGE` | No | Defaults to `{name} has left {server}.` |

Replace every example ID and token before startup. Part 2 explains where to obtain them. Both announcement settings may use the same text channel.

Write values without surrounding quotes, one setting per line. To use a default greeting, omit its setting rather than leaving it empty. Greeting templates support `{mention}`, `{name}`, and `{server}`. Only welcome messages allow the joining member to be pinged.

Keep `.env` private. It is excluded from Git and the image build context. Do not paste tokens into chat or put them in the Dockerfile.

### Step 3: Build and start

```powershell
docker compose up --build -d
docker compose ps
docker compose logs -f --tail 100 bot
```

Look for `Registered 2 commands for server ...` and `Connected as ...`. Run `~help` or `/help` in Discord. Press Ctrl+C to exit the log viewer; the container stays running.

The bot runs as a non-root user. It needs outbound HTTPS/WebSocket access, with no incoming ports or database. Run only one instance with this token. On first startup after switching from `music`, server command synchronization replaces the music commands with the two core commands.

### Step 4: Stop, update, and restart

| Task | Command |
| --- | --- |
| View status | `docker compose ps -a` |
| View recent logs | `docker compose logs --tail 100 bot` |
| Stop and remove the container, keeping the image | `docker compose down` |
| Start the existing image again | `docker compose up -d` |
| Rebuild after code, branch, or dependency changes | `docker compose up --build -d` |
| Apply changes to `.env` | `docker compose up -d --force-recreate` |

Compose restarts the bot after crashes and when Docker restarts, unless you explicitly stopped it. It does not keep the host awake. Logs rotate automatically.

### Step 5: Switch between the core and music versions

Commit or otherwise preserve local code changes before switching branches. Your ignored `.env` stays on your machine.

```powershell
# Use the preserved music implementation.
git switch music
docker compose up --build -d

# Return to the core bot for future development.
git switch main
docker compose up --build -d
```

Both branches use the same Compose service and image name. Rebuilding replaces the running version; do not run both with the same token. Each branch has its own requirements and README. Switching branches alone does not change an already-running container.

### Move to another Docker host (optional)

Copy or clone the desired branch on the host, create a private `.env`, and run `docker compose up --build -d`. Stop the local bot before starting the hosted copy. Rebuild on the target machine when moving between x86 and ARM.

### Local development and tests (optional)

With Python 3.12 installed:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# Load raw environment values from your configured .env.
Get-Content .env | ForEach-Object {
    if ($_ -match '^([A-Z_][A-Z0-9_]*)=(.*)$') {
        [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process')
    }
}
.\.venv\Scripts\python.exe -m bot
```

Use your Python executable instead of `py -3.12` if the launcher is unavailable. Stop the container before starting the local process. A fresh virtual environment is recommended when switching from music if you want to remove its previously installed packages.

Automated tests use mocked Discord interactions and need no token:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

`bot/config.py` validates settings, `bot/membership.py` handles member events, and `bot/__main__.py` starts the bot and registers commands. Future features can be added as cogs.

### Container troubleshooting

| Problem | Fix |
| --- | --- |
| Docker engine unavailable | Start Docker Desktop in Linux container mode and check `docker version`. |
| Container repeatedly restarts | Check logs, required environment settings, token, and intents. |
| Code or branch changes are ignored | Run `docker compose up --build -d`. Restarting alone does not update code. |
| Environment changes are ignored | Run `docker compose up -d --force-recreate`. |

## Part 2: Set up the bot on Discord

You need permission to install applications and manage roles/channels in your server.

### Step 1: Create the application and get its token

1. Open the [Discord Developer Portal](https://discord.com/developers/applications).
2. Create a New Application, or select your existing one.
3. Open **Bot** and copy its token, or select **Reset Token** to generate one.
4. Put the bot token in `DISCORD_TOKEN` in `.env`.

The token is different from the application ID, public key, or client secret. Copy it immediately and keep it private. Resetting it invalidates the old token; update `.env` and recreate the container afterward.

### Step 2: Enable required intents

Under **Bot > Privileged Gateway Intents**, enable:

- **Server Members Intent** for member joins, departures, and roles.
- **Message Content Intent** for prefix commands such as `~help`.

Save changes. Presence Intent is not required. The code enables the same required intents.

### Step 3: Install the bot in your server

1. Under **Installation**, configure **Guild Install** with the `bot` and `applications.commands` scopes.
2. Grant **Manage Roles**, **View Channels**, and **Send Messages**.
3. Open the installation link and authorize it for your intended server.
4. Members using slash commands need **Use Application Commands**.

Administrator, Connect, and Speak permissions are not required for the core bot. If the bot was previously installed with music permissions, you may remove Connect and Speak from its role. Channel permission overrides must allow it to view and send messages in the announcement/command text channels.

### Step 4: Prepare roles and announcement channels

1. Create or choose the default roles for new human members.
2. Move the bot's highest role above every role it assigns under **Server Settings > Roles**.
3. Create or choose the welcome and goodbye text channels. One channel may serve both purposes.
4. Ensure the bot can view and send messages in those channels.

Default roles cannot be `@everyone` or integration-managed roles. They must be below the bot's highest role. Existing members do not receive roles retroactively. Bots are ignored. Human members receive a welcome immediately; role assignment waits until membership screening is complete.

### Step 5: Copy the IDs into `.env`

Enable **User Settings > Advanced > Developer Mode** in Discord.

| Item | Where to copy the ID | Setting |
| --- | --- | --- |
| Server | Right-click the server > Copy Server ID | `GUILD_ID` |
| Default roles | Server Settings > Roles > copy each role ID | `DEFAULT_ROLE_IDS` |
| Welcome channel | Right-click channel > Copy Channel ID | `WELCOME_CHANNEL_ID` |
| Goodbye channel | Right-click channel > Copy Channel ID | `GOODBYE_CHANNEL_ID` |

Separate multiple role IDs with commas, for example `111111111111111111,222222222222222222`. Use numeric IDs rather than names or links. Save `.env` and return to Part 1, Step 3.

### Step 6: Verify behavior

| Text command | Slash command | Behavior |
| --- | --- | --- |
| `~help` | `/help` | List all implemented commands |
| `~ping` | `/ping` | Check connectivity and gateway latency |

Set `COMMAND_PREFIX=~db` if another bot already uses `~`; commands then become `~dbhelp` and `~dbping`. Slash command names stay unchanged. Unknown text commands are ignored, and commands only run in the configured server.

1. Run help and ping, and confirm only the two core commands appear.
2. Join with a human test account and verify the welcome message and default roles.
3. If screening is enabled, confirm roles wait until acceptance.
4. Leave with that account and verify the goodbye message.
5. Restart the container and confirm the bot reconnects.

Departure messages cover leaving, kicking, and banning without distinguishing the reason. Events missed while the bot is offline are not replayed.

### Discord troubleshooting

| Problem | What to check |
| --- | --- |
| Login fails | Verify the token, update `.env`, and recreate the container. |
| Privileged intent error | Enable Server Members Intent and Message Content Intent, save, and restart. |
| Slash commands are missing | Check the server ID, installation scopes, server membership, and Use Application Commands permission. |
| Text commands are ignored | Check Message Content Intent, the prefix, and View Channel/Send Messages permissions. |
| Roles are missing | Check Manage Roles, role IDs, role order, and screening status. |
| Announcements are missing | Check the text-channel IDs and channel permissions. |
