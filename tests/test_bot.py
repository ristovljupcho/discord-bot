import asyncio
from datetime import datetime, timezone
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import discord
from discord.ext import commands

from bot.audio import PlaybackError, Playlist, Track, resolve_playlist, resolve_track, youtube_url
from bot.config import Settings
from bot.membership import Membership
from bot.music import MAX_QUEUE, Music, Request
from bot.__main__ import PersonalBot


ENV = {
    "DISCORD_TOKEN": "test-token",
    "GUILD_ID": "100",
    "DEFAULT_ROLE_IDS": "200,201",
    "WELCOME_CHANNEL_ID": "300",
    "GOODBYE_CHANNEL_ID": "301",
}
URL = "https://www.youtube.com/watch?v=BaW_jenozKc"
PLAYLIST_URL = "https://www.youtube.com/playlist?list=PLtest123456"


class ConfigurationTests(unittest.TestCase):
    def test_prefix_default_and_override(self):
        self.assertEqual(Settings.from_env(ENV).command_prefix, "~")
        self.assertEqual(Settings.from_env({**ENV, "COMMAND_PREFIX": "~db"}).command_prefix, "~db")
        for prefix in ("", " ", "~ ", "/", "`", "x" * 11):
            with self.subTest(prefix=prefix), self.assertRaises(ValueError):
                Settings.from_env({**ENV, "COMMAND_PREFIX": prefix})

    def test_valid_configuration_and_deduplicated_roles(self):
        settings = Settings.from_env({**ENV, "DEFAULT_ROLE_IDS": "200, 201,200"})
        self.assertEqual(settings.default_role_ids, (200, 201))
        self.assertIn("{mention}", settings.welcome_message)

    def test_missing_or_invalid_configuration(self):
        for name in ENV:
            with self.subTest(name=name), self.assertRaises(ValueError):
                Settings.from_env({**ENV, name: ""})
        for value in ("0", "-1", "abc", str(2**64)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Settings.from_env({**ENV, "GUILD_ID": value})

    def test_rejects_unknown_or_unsafe_template_fields(self):
        for value in ("{bad}", "{name.__class__}", "{name!r}", "{name:>2000}", "{", "", "x" * 1501):
            with self.subTest(value=value[:30]), self.assertRaises(ValueError):
                Settings.from_env({**ENV, "WELCOME_MESSAGE": value})


class URLTests(unittest.TestCase):
    def test_supported_links_are_canonicalized(self):
        for value in (
            "https://youtu.be/BaW_jenozKc?si=abc",
            URL + "&t=10",
            "https://m.youtube.com/watch?v=BaW_jenozKc",
            "https://music.youtube.com/watch?v=BaW_jenozKc",
            "https://www.youtube.com/shorts/BaW_jenozKc",
            "https://www.youtube.com/embed/BaW_jenozKc",
        ):
            with self.subTest(value=value):
                self.assertEqual(youtube_url(value), URL)

    def test_playlist_links_are_canonicalized(self):
        for value in (
            PLAYLIST_URL, URL + "&list=PLtest123456&index=5",
            "https://youtu.be/BaW_jenozKc?list=PLtest123456",
            "https://music.youtube.com/playlist?list=PLtest123456",
        ):
            with self.subTest(value=value):
                self.assertEqual(youtube_url(value), PLAYLIST_URL)

    def test_rejects_mixes_invalid_playlists_and_untrusted_inputs(self):
        for value in (
            URL + "&list=RDtest123456", "https://youtube.com/playlist?list=",
            PLAYLIST_URL + "&list=PLother", "https://youtube.com/playlist?list=bad%26id",
            "https://evil.example/playlist?list=PLtest123456", "https://youtube.com/playlist",
            "https://open.spotify.com/track/test", "http://youtu.be/BaW_jenozKc",
            "https://youtube.com.evil.example/watch?v=BaW_jenozKc",
            "https://user:password@youtu.be/BaW_jenozKc", "file:///etc/passwd",
            "https://youtu.be:8080/BaW_jenozKc", "https://youtu.be/short",
            "https://youtu.be/BaW_jenozKc/extra", "https://127.0.0.1/a",
            "https://youtube.com/watch?v=BaW_jenozKc&v=BaW_jenozKc",
            "https://[invalid/", "ytsearch:my song",
        ):
            with self.subTest(value=value), self.assertRaises(PlaybackError):
                youtube_url(value)


class ResolverTests(unittest.IsolatedAsyncioTestCase):
    def process(self, data=None, returncode=0):
        process = SimpleNamespace(
            communicate=AsyncMock(return_value=(json.dumps(data or {}).encode(), b"")),
            returncode=returncode,
            kill=Mock(),
        )
        return process

    async def test_resolves_stream_and_uses_argument_list(self):
        process = self.process({"title": "Test", "url": "https://rr1.googlevideo.com/audio"})
        with patch("bot.audio.asyncio.create_subprocess_exec", AsyncMock(return_value=process)) as spawn:
            track = await resolve_track(URL)
        self.assertEqual(track.title, "Test")
        self.assertIn("--ignore-config", spawn.call_args.args)
        self.assertEqual(spawn.call_args.args[-1], URL)

    async def test_rejects_bad_responses_and_live_streams(self):
        for data in (
            {}, {"url": "https://127.0.0.1/audio"}, {"url": "file:///etc/passwd"},
            {"url": "https://evilgooglevideo.com/audio"}, {"url": "https://rr.googlevideo.com:999/audio"},
            {"url": "https://rr.googlevideo.com/audio", "is_live": True},
            {"url": "https://rr.googlevideo.com/audio", "live_status": "is_upcoming"},
        ):
            with self.subTest(data=data), patch(
                "bot.audio.asyncio.create_subprocess_exec", AsyncMock(return_value=self.process(data))
            ), self.assertRaises(PlaybackError):
                await resolve_track(URL)

    async def test_failed_extraction_is_a_friendly_error(self):
        with patch("bot.audio.asyncio.create_subprocess_exec", AsyncMock(return_value=self.process(returncode=1))):
            with self.assertRaisesRegex(PlaybackError, "could not provide"):
                await resolve_track(URL)

    async def test_playlist_preserves_order_duplicates_and_skips_inaccessible_entries(self):
        process = self.process({"title": "Test playlist", "entries": [
            {"id": "abcdefghijk", "title": "First"},
            {"id": "BaW_jenozKc", "title": "Second"},
            {"id": "abcdefghijk", "title": "Repeated"},
            {"id": "abcdefghijk", "title": "[Private video]"},
            {"id": "abcdefghijk", "availability": "private"},
            {"id": "abcdefghijk", "live_status": "is_live"},
            {"id": "https://127.0.0.1/bad"}, None,
        ]})
        with patch("bot.audio.asyncio.create_subprocess_exec", AsyncMock(return_value=process)) as spawn:
            playlist = await resolve_playlist(PLAYLIST_URL, limit=MAX_QUEUE)
        self.assertEqual(playlist.urls, (
            "https://www.youtube.com/watch?v=abcdefghijk", URL,
            "https://www.youtube.com/watch?v=abcdefghijk",
        ))
        self.assertEqual(playlist.skipped, 5)
        self.assertIn("--flat-playlist", spawn.call_args.args)
        self.assertIn(f"1:{MAX_QUEUE + 1}", spawn.call_args.args)
        self.assertNotIn("--format", spawn.call_args.args)

    async def test_playlist_rejects_empty_malformed_and_over_limit_responses(self):
        for data in ({}, {"entries": []}, {"entries": [None]}, {"entries": [None] * 3}):
            with self.subTest(data=data), patch(
                "bot.audio.asyncio.create_subprocess_exec", AsyncMock(return_value=self.process(data))
            ), self.assertRaises(PlaybackError):
                await resolve_playlist(PLAYLIST_URL, limit=2)

    async def test_track_resolver_does_not_expand_a_playlist(self):
        with patch("bot.audio.asyncio.create_subprocess_exec", AsyncMock()) as spawn:
            with self.assertRaises(PlaybackError):
                await resolve_track(PLAYLIST_URL)
            spawn.assert_not_awaited()

    async def test_timeout_kills_and_reaps_extractor(self):
        process = self.process(returncode=None)
        never = asyncio.Event()
        calls = 0

        async def communicate():
            nonlocal calls
            calls += 1
            if calls == 1:
                await never.wait()
            return b"", b""

        process.communicate.side_effect = communicate
        real_wait = asyncio.wait_for

        async def short_wait(future, timeout):
            return await real_wait(future, .01)

        with patch("bot.audio.asyncio.create_subprocess_exec", AsyncMock(return_value=process)), patch(
            "bot.audio.asyncio.wait_for", side_effect=short_wait
        ):
            with self.assertRaisesRegex(PlaybackError, "too long"):
                await resolve_track(URL)
        process.kill.assert_called_once()
        self.assertEqual(calls, 2)

    async def test_cancellation_kills_and_reaps_extractor(self):
        process = self.process(returncode=None)
        started = asyncio.Event()
        calls = 0

        async def communicate():
            nonlocal calls
            calls += 1
            started.set()
            if calls == 1:
                await asyncio.Event().wait()
            return b"", b""

        process.communicate.side_effect = communicate
        with patch("bot.audio.asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
            task = asyncio.create_task(resolve_track(URL))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        process.kill.assert_called_once()
        self.assertEqual(calls, 2)


def member_fixture():
    member = MagicMock(spec=discord.Member)
    member.id = 400
    member.bot = False
    member.pending = False
    member.roles = []
    member.display_name = "Test member"
    member.mention = "<@400>"
    member.add_roles = AsyncMock()
    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock()
    roles = []
    for role_id in (200, 201):
        role = MagicMock(spec=discord.Role)
        role.id = role_id
        role.is_default.return_value = False
        role.managed = False
        role.__ge__.return_value = False
        roles.append(role)
    member.guild = SimpleNamespace(
        id=100, name="Test server", me=SimpleNamespace(
            guild_permissions=SimpleNamespace(manage_roles=True), top_role=999,
        ), get_role=lambda role_id: next((role for role in roles if role.id == role_id), None),
        get_channel=lambda channel_id: channel,
    )
    return member, roles, channel


class MembershipTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = Membership(Settings.from_env(ENV))
        self.member, self.roles, self.channel = member_fixture()

    async def test_join_assigns_roles_and_announces(self):
        await self.cog.on_member_join(self.member)
        self.member.add_roles.assert_awaited_once_with(*self.roles, reason="Configured default membership roles")
        self.channel.send.assert_awaited_once()
        self.assertIn("Welcome <@400>", self.channel.send.call_args.args[0])

    async def test_pending_member_gets_roles_only_after_screening(self):
        self.member.pending = True
        await self.cog.on_member_join(self.member)
        self.member.add_roles.assert_not_awaited()
        before = SimpleNamespace(pending=True)
        self.member.pending = False
        await self.cog.on_member_update(before, self.member)
        self.member.add_roles.assert_awaited_once()
        self.channel.send.assert_awaited_once()

    async def test_existing_roles_are_not_reassigned(self):
        self.member.roles = self.roles[:1]
        await self.cog.assign_roles(self.member)
        self.member.add_roles.assert_awaited_once_with(self.roles[1], reason="Configured default membership roles")

    async def test_managed_high_and_missing_roles_are_skipped(self):
        self.roles[0].managed = True
        self.roles[1].__ge__.return_value = True
        await self.cog.assign_roles(self.member)
        self.member.add_roles.assert_not_awaited()
        self.member.guild.get_role = lambda _: None
        await self.cog.assign_roles(self.member)
        self.member.add_roles.assert_not_awaited()

    async def test_missing_permission_still_sends_welcome(self):
        self.member.guild.me.guild_permissions.manage_roles = False
        await self.cog.on_member_join(self.member)
        self.member.add_roles.assert_not_awaited()
        self.channel.send.assert_awaited_once()

    async def test_role_http_failure_still_sends_welcome(self):
        self.member.add_roles.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Denied")
        await self.cog.on_member_join(self.member)
        self.channel.send.assert_awaited_once()

    async def test_bots_and_other_servers_are_ignored(self):
        self.member.bot = True
        await self.cog.on_member_join(self.member)
        await self.cog.on_member_remove(self.member)
        self.member.bot = False
        self.member.guild.id = 999
        await self.cog.on_member_join(self.member)
        self.member.add_roles.assert_not_awaited()
        self.channel.send.assert_not_awaited()

    async def test_leave_message_disables_mentions(self):
        await self.cog.on_member_remove(self.member)
        args = self.channel.send.call_args
        self.assertIn("has left", args.args[0])
        self.assertFalse(args.kwargs["allowed_mentions"].everyone)
        self.assertFalse(args.kwargs["allowed_mentions"].users)


class MusicTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cog = Music(Mock(), Settings.from_env(ENV))
        self.member, _, self.channel = member_fixture()
        voice_channel = MagicMock(spec=discord.VoiceChannel)
        voice_channel.id = 500
        voice_channel.members = [self.member]
        voice_channel.permissions_for.return_value = SimpleNamespace(view_channel=True, connect=True, speak=True)
        self.member.voice = SimpleNamespace(channel=voice_channel)
        self.voice = MagicMock(spec=discord.VoiceClient)
        self.voice.channel = voice_channel
        self.voice.is_connected.return_value = True
        self.voice.disconnect = AsyncMock()
        self.cog.voice = self.voice
        self.ctx = SimpleNamespace(
            author=self.member, guild=self.member.guild, channel=self.channel,
            defer=AsyncMock(), send=AsyncMock(),
        )

    async def asyncTearDown(self):
        if self.cog.worker:
            self.cog.worker.cancel()
            await asyncio.gather(self.cog.worker, return_exceptions=True)

    async def test_requires_shared_voice_channel(self):
        self.assertEqual(self.cog.require_voice(self.ctx).id, 500)
        self.voice.channel = SimpleNamespace(id=999)
        with self.assertRaisesRegex(PlaybackError, "Join my voice"):
            self.cog.require_voice(self.ctx)
        self.member.voice = None
        with self.assertRaises(PlaybackError):
            self.cog.require_voice(self.ctx)

    async def test_play_canonicalizes_and_enqueues(self):
        await self.cog.play.callback(self.cog, self.ctx, "https://youtu.be/BaW_jenozKc")
        request = self.cog.queue.get_nowait()
        self.assertEqual(request.url, URL)
        self.ctx.send.assert_awaited_once()

    async def test_queue_limit(self):
        for _ in range(MAX_QUEUE):
            self.cog.queue.put_nowait(Request(URL, self.channel))
        with self.assertRaisesRegex(PlaybackError, "queue is full"):
            await self.cog.play.callback(self.cog, self.ctx, URL)

    async def test_playlist_is_queued_as_individual_tracks_in_order(self):
        second = "https://www.youtube.com/watch?v=abcdefghijk"
        playlist = Playlist("Test", (URL, second), 1)
        with patch("bot.music.resolve_playlist", AsyncMock(return_value=playlist)):
            await self.cog.play.callback(self.cog, self.ctx, PLAYLIST_URL)
        self.assertEqual(self.cog.queue.get_nowait().url, URL)
        self.assertEqual(self.cog.queue.get_nowait().url, second)
        self.assertIn("Queued 2 videos", self.ctx.send.call_args.args[0])
        self.assertIn("Skipped 1", self.ctx.send.call_args.args[0])
        self.assertFalse(self.cog.playlist_tasks)

    async def test_playlist_that_will_not_fit_is_rejected_without_partial_enqueue(self):
        for _ in range(MAX_QUEUE - 1):
            self.cog.queue.put_nowait(Request(URL, self.channel))
        with patch("bot.music.resolve_playlist", AsyncMock(return_value=Playlist("Test", (URL, URL), 0))):
            with self.assertRaisesRegex(PlaybackError, "Nothing was added"):
                await self.cog.play.callback(self.cog, self.ctx, PLAYLIST_URL)
        self.assertEqual(self.cog.queue.qsize(), MAX_QUEUE - 1)

    async def test_playlist_loading_does_not_block_stop_or_enqueue_after_stop(self):
        started = asyncio.Event()

        async def load(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()

        with patch("bot.music.resolve_playlist", side_effect=load):
            command = asyncio.create_task(self.cog.play.callback(self.cog, self.ctx, PLAYLIST_URL))
            await asyncio.wait_for(started.wait(), 1)
            await asyncio.wait_for(self.cog.stop_session(), 1)
            with self.assertRaisesRegex(PlaybackError, "cancelled"):
                await command
        self.assertTrue(self.cog.queue.empty())
        self.assertFalse(self.cog.playlist_tasks)
        self.voice.disconnect.assert_awaited_once()

    async def test_failed_playlist_leaves_existing_queue_intact(self):
        self.cog.queue.put_nowait(Request(URL, self.channel))
        with patch("bot.music.resolve_playlist", AsyncMock(side_effect=PlaybackError("Unavailable"))):
            with self.assertRaises(PlaybackError):
                await self.cog.play.callback(self.cog, self.ctx, PLAYLIST_URL)
        self.assertEqual(self.cog.queue.qsize(), 1)
        self.assertFalse(self.cog.playlist_tasks)

    async def test_queue_progresses_after_failed_track(self):
        first = Request(URL, self.channel)
        second = Request("https://youtu.be/abcdefghijk", self.channel)
        self.cog.play_track = AsyncMock(side_effect=[PlaybackError("Unavailable"), None])
        self.cog.queue.put_nowait(first)
        self.cog.queue.put_nowait(second)
        self.cog.worker = asyncio.create_task(self.cog.run_queue())
        await asyncio.wait_for(self.cog.queue.join(), 1)
        self.assertEqual(self.cog.play_track.await_args_list[0].args, (first,))
        self.assertEqual(self.cog.play_track.await_args_list[1].args, (second,))
        self.channel.send.assert_awaited_once()
        self.assertFalse(self.cog.worker.done())

    async def test_skip_cancels_loading_and_advances_queue(self):
        started = asyncio.Event()
        calls = []

        async def play_track(request):
            calls.append(request)
            if len(calls) == 1:
                started.set()
                await asyncio.Event().wait()

        self.cog.play_track = play_track
        self.cog.queue.put_nowait(Request(URL, self.channel))
        self.cog.queue.put_nowait(Request(URL, self.channel))
        self.cog.worker = asyncio.create_task(self.cog.run_queue())
        await asyncio.wait_for(started.wait(), 1)
        await self.cog.skip.callback(self.cog, self.ctx)
        await asyncio.wait_for(self.cog.queue.join(), 1)
        self.assertEqual(len(calls), 2)
        self.assertFalse(self.cog.worker.done())

    async def test_stop_clears_queue_cancels_loading_and_disconnects(self):
        started = asyncio.Event()

        async def play_track(_):
            started.set()
            await asyncio.Event().wait()

        self.cog.play_track = play_track
        self.cog.queue.put_nowait(Request(URL, self.channel))
        self.cog.queue.put_nowait(Request(URL, self.channel))
        self.cog.worker = asyncio.create_task(self.cog.run_queue())
        await asyncio.wait_for(started.wait(), 1)
        await self.cog.stop_session()
        await asyncio.wait_for(self.cog.queue.join(), 1)
        self.voice.disconnect.assert_awaited_once_with(force=True)
        self.assertIsNone(self.cog.voice)
        self.assertIsNone(self.cog.current)

    async def test_idle_and_empty_voice_disconnect_after_five_minutes(self):
        for empty in (False, True):
            with self.subTest(empty=empty):
                self.cog.voice = self.voice
                self.cog.idle_since = self.cog.empty_since = None
                self.voice.channel.members = [] if empty else [self.member]
                self.cog.current = Request(URL, self.channel) if empty else None
                with patch("bot.music.time.monotonic", return_value=100):
                    await self.cog.monitor.coro(self.cog)
                self.assertIsNotNone(self.cog.voice)
                with patch("bot.music.time.monotonic", return_value=399):
                    await self.cog.monitor.coro(self.cog)
                self.assertIsNotNone(self.cog.voice)
                with patch("bot.music.time.monotonic", return_value=400):
                    await self.cog.monitor.coro(self.cog)
                self.assertIsNone(self.cog.voice)

    async def test_playback_callback_failure_cleans_up_audio(self):
        source = Mock()

        def play(_, *, after, bitrate):
            after(RuntimeError("audio failed"))

        self.voice.play.side_effect = play
        with patch("bot.music.resolve_track", AsyncMock(return_value=Track("Test", "https://rr.googlevideo.com/a"))), patch(
            "bot.music.discord.FFmpegPCMAudio", return_value=source
        ):
            with self.assertRaisesRegex(PlaybackError, "playback failed"):
                await self.cog.play_track(Request(URL, self.channel))
        source.cleanup.assert_called_once()
        self.voice.stop.assert_called_once()

    async def test_pause_resume_and_empty_queue(self):
        self.voice.is_playing.return_value = True
        await self.cog.pause.callback(self.cog, self.ctx)
        self.voice.pause.assert_called_once()
        self.voice.is_paused.return_value = True
        await self.cog.resume.callback(self.cog, self.ctx)
        self.voice.resume.assert_called_once()
        await self.cog.show_queue.callback(self.cog, self.ctx)
        self.assertEqual(self.ctx.send.call_args.args[0], "The queue is empty.")

    async def test_controls_acknowledge_before_waiting_for_connection_lock(self):
        for command in (self.cog.pause, self.cog.resume, self.cog.skip):
            with self.subTest(command=command.name):
                self.ctx.defer.reset_mock()
                await self.cog.lock.acquire()
                task = asyncio.create_task(command.callback(self.cog, self.ctx))
                try:
                    await asyncio.sleep(0)
                    self.ctx.defer.assert_awaited_once()
                    self.assertFalse(task.done())
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    self.cog.lock.release()

    async def test_queue_preview_keeps_fifo_order(self):
        first = Request(URL, self.channel)
        second = Request("https://www.youtube.com/watch?v=abcdefghijk", self.channel)
        self.cog.queue.put_nowait(first)
        self.cog.queue.put_nowait(second)
        await self.cog.show_queue.callback(self.cog, self.ctx)
        self.assertEqual(self.cog.queue.get_nowait(), first)
        self.assertEqual(self.cog.queue.get_nowait(), second)

    async def test_silent_voice_disconnect_ends_playback_and_cleans_up(self):
        source = Mock()
        connected = True
        self.voice.is_connected.side_effect = lambda: connected
        real_wait = asyncio.wait_for

        async def short_wait(future, timeout):
            nonlocal connected
            connected = False
            return await real_wait(future, .01)

        with patch("bot.music.resolve_track", AsyncMock(return_value=Track("Test", "https://rr.googlevideo.com/a"))), patch(
            "bot.music.discord.FFmpegPCMAudio", return_value=source
        ), patch("bot.music.asyncio.wait_for", side_effect=short_wait):
            with self.assertRaisesRegex(PlaybackError, "connection was lost"):
                await self.cog.play_track(Request(URL, self.channel))
        source.cleanup.assert_called_once()

    async def test_extracting_while_shutdown_cancels_worker_and_disconnects(self):
        started = asyncio.Event()

        async def play_track(_):
            started.set()
            await asyncio.Event().wait()

        self.cog.play_track = play_track
        self.cog.queue.put_nowait(Request(URL, self.channel))
        self.cog.worker = asyncio.create_task(self.cog.run_queue())
        await asyncio.wait_for(started.wait(), 1)
        await self.cog.cog_unload()
        self.assertTrue(self.cog.worker.cancelled())
        self.voice.disconnect.assert_awaited_once_with(force=True)


class ApplicationTests(unittest.IsolatedAsyncioTestCase):
    async def make_bot(self, prefix="~"):
        bot = PersonalBot(Settings.from_env({**ENV, "COMMAND_PREFIX": prefix}))
        await bot.__aenter__()
        self.addAsyncCleanup(bot.__aexit__, None, None, None)
        with patch.object(bot.tree, "sync", AsyncMock(return_value=[])):
            await bot.setup_hook()
        bot._connection.user = SimpleNamespace(id=999)
        return bot

    async def context(self, bot, content):
        member, _, channel = member_fixture()
        message = MagicMock(spec=discord.Message)
        message.content = content
        message.author = member
        message.guild = member.guild
        message.channel = channel
        message._state = bot._connection
        message.attachments = []
        message.edited_at = None
        message.created_at = datetime.now(timezone.utc)
        return await bot.get_context(message)

    async def test_help_parses_and_lists_every_command(self):
        bot = await self.make_bot()
        ctx = await self.context(bot, "~help")
        self.assertTrue(ctx.valid)
        with patch.object(commands.Context, "send", AsyncMock()) as send:
            await ctx.command.invoke(ctx)
        text = send.call_args.args[0]
        for command in bot.walk_commands():
            self.assertIn(f"~{command.name}", text)
        self.assertIn("~play <url>", text)
        self.assertLessEqual(len(text), 2000)

    async def test_prefix_play_uses_existing_queue_and_matches_slash_command(self):
        bot = await self.make_bot()
        ctx = await self.context(bot, f"~play {URL}")
        channel = MagicMock(spec=discord.VoiceChannel)
        channel.id = 500
        ctx.author.voice = SimpleNamespace(channel=channel)
        voice = MagicMock(spec=discord.VoiceClient)
        voice.is_connected.return_value = True
        voice.channel = channel
        voice.disconnect = AsyncMock()
        bot.music.voice = voice
        bot.music.worker.cancel()
        await asyncio.gather(bot.music.worker, return_exceptions=True)
        with patch.object(commands.Context, "send", AsyncMock()) as send:
            await ctx.command.invoke(ctx)
        self.assertEqual(bot.music.queue.get_nowait().url, URL)
        bot.music.queue.task_done()
        self.assertIn("Queued", send.call_args.args[0])
        slash = bot.tree.get_command("play", guild=discord.Object(id=100))
        self.assertIs(slash.wrapped, bot.get_command("play"))

    async def test_custom_prefix_ignores_other_prefixes_and_embedded_commands(self):
        bot = await self.make_bot(prefix="~db")
        self.assertTrue((await self.context(bot, "~dbhelp")).valid)
        for content in ("~help", "!help", "hello ~dbhelp", "~dbhelper"):
            with self.subTest(content=content):
                self.assertFalse((await self.context(bot, content)).valid)

    async def test_commands_reject_dms_and_other_servers(self):
        bot = await self.make_bot()
        for guild in (None, SimpleNamespace(id=999)):
            with self.subTest(guild=guild), self.assertRaises(commands.CheckFailure):
                await bot.can_run(SimpleNamespace(guild=guild))

    async def test_missing_link_reports_usage_and_unknown_commands_are_silent(self):
        bot = await self.make_bot()
        ctx = await self.context(bot, "~play")
        with self.assertRaises(commands.MissingRequiredArgument) as raised:
            await ctx.command.invoke(ctx)
        with patch.object(commands.Context, "send", AsyncMock()) as send:
            await bot.on_command_error(ctx, raised.exception)
            self.assertIn("~play <url>", send.call_args.args[0])
            send.reset_mock()
            await bot.on_command_error(ctx, commands.CommandNotFound())
            send.assert_not_awaited()

    async def test_hybrid_playback_errors_keep_the_friendly_message(self):
        bot = await self.make_bot()
        ctx = await self.context(bot, "~play")
        original = PlaybackError("Join my voice channel to control music.")
        error = commands.HybridCommandError(
            discord.app_commands.CommandInvokeError(bot.get_command("play").app_command, original)
        )
        with patch.object(commands.Context, "send", AsyncMock()) as send:
            await bot.on_command_error(ctx, error)
        self.assertEqual(send.call_args.args[0], str(original))

    async def test_registers_guild_commands_and_shuts_down_background_tasks(self):
        async with PersonalBot(Settings.from_env(ENV)) as bot:
            with patch.object(bot.tree, "sync", AsyncMock(return_value=[])):
                await bot.setup_hook()
            commands = bot.tree.get_commands(guild=discord.Object(id=100))
            self.assertEqual({command.name for command in commands}, {
                "help", "ping", "play", "pause", "resume", "skip", "queue", "nowplaying", "stop",
            })
            self.assertTrue(bot.intents.members)
            self.assertTrue(bot.intents.message_content)
            self.assertTrue(bot.intents.guild_messages)
            worker = bot.music.worker
        self.assertTrue(worker.done())


if __name__ == "__main__":
    unittest.main()
