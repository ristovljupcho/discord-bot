# Personal Discord bot

A bot for one personal Discord server, with automatic member roles, welcome/goodbye messages, and YouTube music playback.

1. [Run the container](#part-1-run-the-container)
2. [Set up the bot on Discord](#part-2-set-up-the-bot-on-discord)

**First-time setup:** prepare Docker and the `.env` file in Part 1, complete Part 2 to obtain your bot token and server IDs, then return to Part 1 to start the container. If Discord is already configured, follow Part 1 directly.

## Part 1: Run the container

### Step 1: Check prerequisites

You need Docker Desktop running with **Linux containers**, Docker Compose **2.30 or later**, and an internet connection. Keep your computer awake and Docker running while the bot is online.

Open PowerShell in the project folder:

```powershell
Set-Location C:\Users\risto\projects\discord-bot
docker version
docker compose version
```

`docker version` must show both **Client** and **Server** information. If the Server section fails or `dockerDesktopLinuxEngine` is missing, open Docker Desktop and wait until the engine is running.

Docker installs Python 3.12, FFmpeg, Opus, Deno, and the pinned Python packages when building the image. You do not need to install them separately on Windows. Starting a container does not reinstall the packages.

### Step 2: Create and fill in the environment file

Copy the example only if you do not already have a `.env` file:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

Fill in these settings. [Part 2](#part-2-set-up-the-bot-on-discord) explains how to obtain each value.

| Setting | Required? | Value |
| --- | --- | --- |
| `DISCORD_TOKEN` | Yes | Your application's bot token |
| `COMMAND_PREFIX` | No | Text-command prefix; defaults to `~`. Change it if another bot uses the same prefix. |
| `GUILD_ID` | Yes | Your Discord server's numeric ID |
| `DEFAULT_ROLE_IDS` | Yes | One or more numeric role IDs, separated by commas |
| `WELCOME_CHANNEL_ID` | Yes | Numeric ID of the welcome text channel |
| `GOODBYE_CHANNEL_ID` | Yes | Numeric ID of the goodbye text channel |
| `WELCOME_MESSAGE` | No | Welcome template; defaults to `Welcome {mention} to {server}!` |
| `GOODBYE_MESSAGE` | No | Goodbye template; defaults to `{name} has left {server}.` |

Replace every example ID and the example token before starting the bot. Welcome and goodbye channels may be the same channel. Use IDs rather than channel names, role names, or Discord links.

Write values without surrounding quotes and keep each value on one line. Compose reads `.env` in raw mode so `$` and template characters are preserved. To use a greeting's default, omit its setting rather than leaving an empty value.

Greeting placeholders:

| Placeholder | Value |
| --- | --- |
| `{mention}` | The member's mention; only welcome messages allow that member to be pinged |
| `{name}` | The member's display name |
| `{server}` | The server's name |

Keep the bot token private. `.env` is excluded from Git and the Docker build context; do not put the token in `Dockerfile`, `compose.yaml`, or chat.

### Step 3: Build and start

After saving the completed `.env`, run:

```powershell
docker compose up --build -d
docker compose ps
docker compose logs -f --tail 100 bot
```

The first build downloads the image and its dependencies. Startup logs should show `Registered 9 commands for server ...` followed by `Connected as ...`. In Discord, run `~help` or `/help` to verify the bot responds.

**Updating from the slash-only version:** enable **Message Content Intent** in Developer Portal > your application > Bot > Privileged Gateway Intents before rebuilding. Keep Server Members Intent enabled too, then run `docker compose up --build -d`. Restarting alone does not pick up changed Python code.

Press **Ctrl+C** to leave the log viewer. The container continues running.

The container runs as a non-root user. It needs outbound HTTPS/WebSocket and Discord voice UDP access; no incoming ports, database, or bind mounts are required. Run only one instance with this bot token.

### Step 4: Stop, restart, and update

Run these commands from the project folder:

| Task | Command |
| --- | --- |
| View recent logs | `docker compose logs --tail 100 bot` |
| Follow logs | `docker compose logs -f bot` |
| View container status, including stopped containers | `docker compose ps -a` |
| Stop and remove the container, keeping the image | `docker compose down` |
| Start again with the existing image | `docker compose up -d` |
| Rebuild after changing code or requirements | `docker compose up --build -d` |
| Apply changes to `.env` | `docker compose up -d --force-recreate` |

Compose restarts the container after unexpected exits and when Docker restarts, unless you explicitly stopped it. It does not keep the computer awake. Logs rotate automatically, and queued songs are lost when the bot restarts.

### Step 5: Move the image to another machine (optional)

Build and export the image:

```powershell
docker build -t personal-discord-bot:local .
docker save -o discord-bot-image.tar personal-discord-bot:local
```

On a machine with the same CPU architecture, copy the archive and supply a separate `.env` file, then run:

```powershell
docker load -i discord-bot-image.tar
docker run -d --name discord-bot --restart unless-stopped --env-file .env --init personal-discord-bot:local
```

Stop the original instance before starting the new one. When moving between x86 and ARM, copy the source and rebuild on the target machine instead.

### Container troubleshooting

| Problem | Fix |
| --- | --- |
| Cannot connect to Docker / Linux engine is missing | Start Docker Desktop in Linux-container mode. Wait for `docker version` to show Server information. |
| `.env` is missing | Create it in the project folder using Step 2. |
| Container repeatedly restarts | Read `docker compose logs --tail 100 bot`. Check all required environment values and the Discord settings in Part 2. |
| Environment changes are ignored | Save `.env`, then run `docker compose up -d --force-recreate`. |
| Code changes are ignored | Run `docker compose up --build -d`. |

### Development and dependency checks (optional)

<details>
<summary>Check the built image without logging in to Discord</summary>

```powershell
docker run --rm personal-discord-bot:local python -c "import discord, nacl, davey, yt_dlp; print(discord.__version__)"
docker run --rm personal-discord-bot:local ffmpeg -version
docker run --rm personal-discord-bot:local deno --version
```

</details>

<details>
<summary>Run locally without Docker and run automated tests</summary>

Install Python 3.12 and FFmpeg on PATH. Windows voice support bundles Opus; on Debian/Ubuntu install `ffmpeg libopus0`. Deno is installed by `requirements.txt`.

From the project folder in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:Path = "$PWD\.venv\Scripts;$env:Path"

# Load raw environment values from your configured .env.
Get-Content .env | ForEach-Object {
    if ($_ -match '^([A-Z_][A-Z0-9_]*)=(.*)$') {
        [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process')
    }
}

.\.venv\Scripts\python.exe -m bot
```

If the Python launcher is unavailable, use your Python 3.12 executable instead of `py -3.12`. Stop the Docker instance before running locally.

Run automated tests separately; they use mocked Discord interactions and do not need a token:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Application structure: `bot/config.py` validates settings, `membership.py` handles member events, `audio.py` resolves YouTube streams in a cancellable subprocess, and `music.py` manages voice and the queue. `bot/__main__.py` starts the bot and registers commands. Future features can be added as cogs.

</details>

## Part 2: Set up the bot on Discord

You need permission to add applications and manage roles/channels in your personal server. Complete these steps once; use the resulting values in Part 1's `.env` file.

### Step 1: Create an application and obtain its bot token

1. Open the [Discord Developer Portal](https://discord.com/developers/applications).
2. Create a **New Application** and give it a name.
3. Open the application's **Bot** page.
4. Copy the bot token, or use **Reset Token** to generate one.
5. Paste it into `DISCORD_TOKEN` in `.env`.

Use the **bot token**, not the application ID, public key, or client secret. If a token is exposed, reset it in the portal, update `.env`, and recreate the container.

### Step 2: Enable member events and text commands

On the **Bot** page, enable both **Server Members Intent** and **Message Content Intent** under **Privileged Gateway Intents**, then save your changes.

Server Members Intent enables member events for role assignment and welcome/goodbye messages. Message Content Intent lets the bot read commands such as `~help` and `~play <link>`. Presence Intent is not required. Both required intents are also enabled in the code.

### Step 3: Invite the bot to your server

1. Open **Installation** and configure **Guild Install**.
2. Include the `bot` and `applications.commands` scopes.
3. Give the bot these permissions: **Manage Roles**, **View Channels**, **Send Messages**, **Connect**, and **Speak**.
4. Open the generated installation link and select your personal server.
5. Authorize the installation.

Administrator permission is not required. Members who use slash commands need **Use Application Commands**. Check channel-specific permission overrides as well as server-level permissions.

### Step 4: Prepare the roles and channels

In your Discord server:

1. Create or choose the ordinary member roles that new members should receive.
2. Under **Server Settings > Roles**, move the bot's highest role **above every role it should assign**. It cannot assign `@everyone`, integration-managed roles, or roles at/above its own highest role.
3. Create or choose the welcome and goodbye **text channels**. One channel may serve both purposes.
4. Allow the bot to view and send messages in those text channels.
5. Allow the bot to view, connect, and speak in the regular voice channels where you want music.

Choose default roles carefully: every new human member will receive them. Existing members are not assigned roles retroactively. Bots are ignored. Members completing membership screening receive roles only after they finish screening; their welcome message is sent when they join.

### Step 5: Copy the server, role, and channel IDs

Enable **User Settings > Advanced > Developer Mode** in Discord, then collect:

| Discord item | How to obtain its ID | `.env` setting |
| --- | --- | --- |
| Personal server | Right-click the server > Copy Server ID | `GUILD_ID` |
| Each default role | Open Server Settings > Roles and copy the role ID | `DEFAULT_ROLE_IDS` |
| Welcome text channel | Right-click the channel > Copy Channel ID | `WELCOME_CHANNEL_ID` |
| Goodbye text channel | Right-click the channel > Copy Channel ID | `GOODBYE_CHANNEL_ID` |

For several roles, separate IDs with commas, for example `111111111111111111,222222222222222222`.

Save the values in `.env`, then [return to Part 1, Step 3](#step-3-build-and-start). If the container is already running, apply the changed environment with `docker compose up -d --force-recreate`.

### Step 6: Use and verify the bot

Slash commands are registered only in the configured `GUILD_ID` during startup. Text commands also work only in that server. Use `~` immediately followed by the command name; `~play` takes one YouTube link after a space:

```text
~help
~play https://www.youtube.com/watch?v=jNQXAC9IVRw
~play https://www.youtube.com/playlist?list=YOUR_PLAYLIST_ID
```

`~help` lists all implemented commands automatically. Each command also has a `/` equivalent, sharing the same behavior and music queue.

| Text command | Slash command | What it does |
| --- | --- | --- |
| `~help` | `/help` | List all available commands and usage |
| `~ping` | `/ping` | Check connection and gateway latency |
| `~play <link>` | `/play url` | Join your regular voice channel and queue a YouTube video or playlist |
| `~pause` | `/pause` | Pause the current video |
| `~resume` | `/resume` | Resume paused audio |
| `~skip` | `/skip` | Skip the current video, including one still loading |
| `~queue` | `/queue` | Show the current video and first ten queued links |
| `~nowplaying` | `/nowplaying` | Show the current video or loading status |
| `~stop` | `/stop` | Clear the queue, stop audio, and leave voice |

If another bot uses `~`, set a distinct prefix such as `COMMAND_PREFIX=~db` in `.env` and recreate the container. Commands then become `~dbhelp`, `~dbplay <link>`, and so on; slash names stay unchanged. Only the configured text prefix is recognized, and unknown command names are ignored. Two bots that recognize the same prefix and command may both respond, so check your server's other bots when choosing it.

Check the setup in your server:

1. Run `~help`, `~ping`, and `/help`. Confirm that help lists all nine commands and the slash commands still appear.
2. Join with a human test account. Confirm the welcome message and default roles. If screening is enabled, verify that roles wait until acceptance.
3. Leave with that account and confirm the goodbye message.
4. Join a regular voice channel and queue two permitted YouTube videos or a public playlist. Confirm playlist order, then try pause, resume, skip, queue, nowplaying, and stop. Try stop during playlist loading too.
5. Attempt music controls from a different voice channel; they should be rejected.
6. Queue an unavailable video followed by a playable video. Confirm the failure is reported, the queue continues, and membership messages still work.
7. Let the queue empty, then separately leave voice while music is playing. In each case, check that the bot disconnects after about five minutes.
8. Restart the container. Confirm the bot reconnects and the previous queue is gone.

All music commands require joining the bot's voice channel. The first `/play` selects that channel; the bot stays there until disconnected. The queue accepts up to 1,000 upcoming videos. It disconnects after five minutes with no current/queued track or playlist being loaded, or five minutes without human listeners, checked every 15 seconds. Paused music counts as a current track.

### Supported music and membership behavior

Use HTTPS YouTube video links (`youtube.com/watch?v=...`, `youtu.be/...`, `/shorts/...`, or `/embed/...`) or playlist links (`youtube.com/playlist?list=...`), including mobile and YouTube Music links.

**Playlist behavior:** a video link containing `list=...` queues the entire playlist from the beginning, ignoring its video position or `index` parameter. Remove `list` if you only want that video. Public and accessible unlisted playlists are supported; private playlists requiring a login are not. Replace `YOUR_PLAYLIST_ID` in the example with your playlist's real ID.

Playlist tracks are appended in their original order, preserving repeated tracks. Entries identified as private, deleted, live, or otherwise unavailable are skipped, and the bot reports that count. If a queued track fails later, playback continues with the next track. Audio streams are resolved individually when each track reaches playback, rather than all at once.

Playlists may contain up to 1,000 entries and must fit within the remaining queue space. If a playlist is too large or will not fit, the bot explains the limit and adds none of it; it never silently truncates the list. `~stop` cancels pending playlist loading, clears the queue, and disconnects. Imports time out after 60 seconds; an excessively slow playlist may need to be retried.

Search terms, automatically generated YouTube Mixes (`RD...` lists), live streams, Spotify, Stage channels, and arbitrary audio URLs are not supported. Private or restricted videos may not play. YouTube extraction is unofficial and may fail because of source restrictions, anti-bot checks, network blocking, or upstream changes. Review the [yt-dlp documentation](https://github.com/yt-dlp/yt-dlp) when updating its pinned dependencies, then rebuild the image. Discord voice uses DAVE support from `discord.py[voice]` and `davey`.

Use content you have permission to play and respect the service's terms. YouTube's [API policies](https://developers.google.com/youtube/terms/developer-policies) prohibit separating audio through their API; this bot does not use that API. There is no login-cookie or authentication-bypass setup.

Goodbye messages cover leaving, kicking, and banning without distinguishing the reason. Events missed while the bot is offline are not replayed by this application.

### Discord troubleshooting

| Problem | What to check |
| --- | --- |
| Discord login fails | Verify the bot token in Developer Portal, update `.env`, and recreate the container. |
| Privileged intent error | Enable both Server Members Intent and Message Content Intent on the application's Bot page, save, and restart the bot. |
| Text commands do not respond | Enable Message Content Intent, check `COMMAND_PREFIX`, and rebuild after updating the code. Start the message with the configured prefix. The bot needs View Channel and Send Messages in the text channel. |
| Slash commands are missing | Check `GUILD_ID`, server membership, the `applications.commands` installation scope, and Use Application Commands permission. Read the logs for registration errors. |
| Default roles are missing | Check role IDs, Manage Roles, role ordering, and whether the member is still completing screening. |
| Welcome/goodbye messages are missing | Check the text-channel IDs and View Channel/Send Messages permissions, including channel overrides. |
| The bot cannot join or speak | Use a regular voice channel. Check View Channel, Connect, and Speak permissions. |
| YouTube playback fails | Try another public video or accessible playlist. Remove `list` to request only one video. Check the supported-music limitations above. |
