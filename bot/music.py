import asyncio
from dataclasses import dataclass
import logging
import subprocess
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot.audio import PlaybackError, Track, resolve_track, youtube_url
from bot.config import Settings

log = logging.getLogger(__name__)
IDLE_SECONDS = 300
MAX_QUEUE = 50


@dataclass(frozen=True)
class Request:
    url: str
    channel: discord.abc.Messageable


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot, settings: Settings):
        self.bot = bot
        self.settings = settings
        self.queue: asyncio.Queue[Request] = asyncio.Queue(maxsize=MAX_QUEUE)
        self.voice: discord.VoiceClient | None = None
        self.current: Request | None = None
        self.track: Track | None = None
        self.active_task: asyncio.Task | None = None
        self.worker: asyncio.Task | None = None
        self.lock = asyncio.Lock()
        self.idle_since: float | None = None
        self.empty_since: float | None = None

    async def cog_load(self) -> None:
        self.worker = asyncio.create_task(self.run_queue(), name="music-queue")
        self.monitor.start()

    async def cog_unload(self) -> None:
        self.monitor.cancel()
        if self.worker:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
        async with self.lock:
            await self.stop_session()

    def require_voice(self, ctx: commands.Context, *, connecting: bool = False):
        member = ctx.author
        if (
            (ctx.guild is None or ctx.guild.id != self.settings.guild_id)
            or not isinstance(member, discord.Member)
            or member.voice is None or member.voice.channel is None
        ):
            raise PlaybackError("Join a voice channel in this server first.")
        channel = member.voice.channel
        if not isinstance(channel, discord.VoiceChannel):
            raise PlaybackError("Use a regular voice channel; Stage channels are not supported.")
        connected = self.voice is not None and self.voice.is_connected()
        if connected and self.voice.channel.id != channel.id:
            raise PlaybackError("Join my voice channel to control music.")
        if not connected and not connecting:
            raise PlaybackError("I am not connected to a voice channel. Use the play command first.")
        return channel

    async def notify(self, request: Request, message: str) -> None:
        try:
            await request.channel.send(message, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            log.warning("Could not send a music notification.")

    async def play_track(self, request: Request) -> None:
        self.track = await resolve_track(request.url)
        if self.voice is None or not self.voice.is_connected():
            raise PlaybackError("The voice connection was lost. Use the play command to reconnect.")
        source = discord.FFmpegPCMAudio(
            self.track.stream_url,
            before_options=(
                "-nostdin -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 "
                "-rw_timeout 15000000 -protocol_whitelist https,tls,tcp,crypto,http"
            ),
            options="-vn -loglevel error",
            stderr=subprocess.DEVNULL,
        )
        finished = asyncio.Event()
        playback_error = []
        loop = asyncio.get_running_loop()

        def after(error: Exception | None) -> None:
            if error:
                playback_error.append(error)
            loop.call_soon_threadsafe(finished.set)

        try:
            self.voice.play(source, after=after, bitrate=96)
            title = discord.utils.escape_markdown(self.track.title)
            await self.notify(request, f"Now playing: **{title}**\n<{request.url}>")
            # Voice disconnection does not always call the audio player's callback.
            while not finished.is_set():
                try:
                    await asyncio.wait_for(finished.wait(), timeout=2)
                except TimeoutError:
                    if self.voice is None or not self.voice.is_connected():
                        raise PlaybackError("The voice connection was lost. Use the play command to reconnect.")
            if playback_error:
                raise PlaybackError("Audio playback failed. Trying the next queued video.")
        finally:
            if self.voice:
                self.voice.stop()
            source.cleanup()

    async def run_queue(self) -> None:
        while True:
            request = await self.queue.get()
            self.current = request
            self.idle_since = None
            self.active_task = asyncio.create_task(self.play_track(request))
            try:
                await self.active_task
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
                # Cancelling only the active track implements /skip and /stop.
            except PlaybackError as exc:
                await self.notify(request, str(exc))
            except Exception as exc:
                log.error("Music track failed (%s).", type(exc).__name__)
                await self.notify(request, "Could not play this video. Trying the next queued video.")
            finally:
                self.active_task = None
                self.current = None
                self.track = None
                self.queue.task_done()

    async def stop_session(self) -> None:
        while not self.queue.empty():
            self.queue.get_nowait()
            self.queue.task_done()
        if self.active_task:
            self.active_task.cancel()
            await asyncio.gather(self.active_task, return_exceptions=True)
        voice, self.voice = self.voice, None
        self.idle_since = self.empty_since = None
        if voice:
            voice.stop()
            try:
                await voice.disconnect(force=True)
            except discord.DiscordException:
                log.warning("Discord rejected voice disconnect.")

    @tasks.loop(seconds=15)
    async def monitor(self) -> None:
        async with self.lock:
            if self.voice is None:
                return
            if not self.voice.is_connected():
                await self.stop_session()
                return
            now = time.monotonic()
            idle = self.current is None and self.queue.empty()
            empty = not any(not member.bot for member in self.voice.channel.members)
            self.idle_since = (self.idle_since if self.idle_since is not None else now) if idle else None
            self.empty_since = (self.empty_since if self.empty_since is not None else now) if empty else None
            if any(start is not None and now - start >= IDLE_SECONDS
                   for start in (self.idle_since, self.empty_since)):
                await self.stop_session()

    @monitor.before_loop
    async def before_monitor(self) -> None:
        await self.bot.wait_until_ready()

    @commands.hybrid_command(name="play", description="Queue one YouTube video and join your voice channel.")
    @app_commands.describe(url="An HTTPS YouTube video link (playlists are not supported)")
    async def play(self, ctx: commands.Context, url: str) -> None:
        canonical = youtube_url(url)
        await ctx.defer()
        async with self.lock:
            channel = self.require_voice(ctx, connecting=True)
            if ctx.channel is None:
                raise PlaybackError("Run this command in a server text channel.")
            if self.queue.full():
                raise PlaybackError("The queue is full (50 videos). Try again after a song finishes.")
            if self.voice is None or not self.voice.is_connected():
                me = ctx.guild.me
                permissions = channel.permissions_for(me)
                if not permissions.view_channel or not permissions.connect or not permissions.speak:
                    raise PlaybackError("I need View Channel, Connect, and Speak in your voice channel.")
                try:
                    if self.voice:
                        await self.voice.disconnect(force=True)
                    self.voice = await channel.connect(timeout=20, reconnect=True, self_deaf=True)
                except (discord.DiscordException, TimeoutError):
                    raise PlaybackError("Could not join voice. Check my permissions and try again.") from None
            self.queue.put_nowait(Request(canonical, ctx.channel))
            position = self.queue.qsize()
        await ctx.send(
            f"Queued at position {position}: <{canonical}>",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @commands.hybrid_command(name="pause", description="Pause the current video.")
    async def pause(self, ctx: commands.Context) -> None:
        await ctx.defer()
        async with self.lock:
            self.require_voice(ctx)
            if not self.voice.is_playing():
                raise PlaybackError("There is no playing audio to pause.")
            self.voice.pause()
        await ctx.send("Paused.")

    @commands.hybrid_command(name="resume", description="Resume paused audio.")
    async def resume(self, ctx: commands.Context) -> None:
        await ctx.defer()
        async with self.lock:
            self.require_voice(ctx)
            if not self.voice.is_paused():
                raise PlaybackError("There is no paused audio to resume.")
            self.voice.resume()
        await ctx.send("Resumed.")

    @commands.hybrid_command(name="skip", description="Skip the current video, including one still loading.")
    async def skip(self, ctx: commands.Context) -> None:
        await ctx.defer()
        async with self.lock:
            self.require_voice(ctx)
            if self.active_task is None:
                raise PlaybackError("There is no current video to skip.")
            self.active_task.cancel()
            await asyncio.gather(self.active_task, return_exceptions=True)
        await ctx.send("Skipped.")

    @commands.hybrid_command(name="queue", description="Show the current video and upcoming links.")
    async def show_queue(self, ctx: commands.Context) -> None:
        self.require_voice(ctx)
        lines = []
        if self.current:
            lines.append(f"Current: <{self.current.url}>")
        # Snapshot without draining the queue or changing FIFO order.
        upcoming = list(self.queue._queue)
        lines.extend(f"{index}. <{item.url}>" for index, item in enumerate(upcoming[:10], 1))
        if len(upcoming) > 10:
            lines.append(f"...and {len(upcoming) - 10} more.")
        await ctx.send("\n".join(lines) or "The queue is empty.")

    @commands.hybrid_command(name="nowplaying", description="Show the current video.")
    async def nowplaying(self, ctx: commands.Context) -> None:
        self.require_voice(ctx)
        if self.current is None:
            text = "Nothing is playing."
        else:
            title = discord.utils.escape_markdown(self.track.title) if self.track else "Loading video..."
            text = f"{title}\n<{self.current.url}>"
        await ctx.send(text, allowed_mentions=discord.AllowedMentions.none())

    @commands.hybrid_command(name="stop", description="Clear the queue, stop audio, and leave voice.")
    async def stop(self, ctx: commands.Context) -> None:
        await ctx.defer()
        async with self.lock:
            self.require_voice(ctx)
            await self.stop_session()
        await ctx.send("Stopped, cleared the queue, and disconnected.")
