from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from discord.ext import commands

from bot.config import Settings
from bot.membership import Membership
from bot.__main__ import PersonalBot


ENV = {
    "DISCORD_TOKEN": "test-token",
    "GUILD_ID": "100",
    "DEFAULT_ROLE_IDS": "200,201",
    "WELCOME_CHANNEL_ID": "300",
    "GOODBYE_CHANNEL_ID": "301",
}
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
        self.assertNotIn("~play", text)
        self.assertLessEqual(len(text), 2000)

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

    async def test_unknown_music_commands_are_ignored(self):
        bot = await self.make_bot()
        ctx = await self.context(bot, "~play")
        self.assertFalse(ctx.valid)
        with patch.object(commands.Context, "send", AsyncMock()) as send:
            await bot.on_command_error(ctx, commands.CommandNotFound())
            send.assert_not_awaited()

    async def test_registers_only_core_commands_without_voice_intents(self):
        async with PersonalBot(Settings.from_env(ENV)) as bot:
            with patch.object(bot.tree, "sync", AsyncMock(return_value=[])):
                await bot.setup_hook()
            commands = bot.tree.get_commands(guild=discord.Object(id=100))
            self.assertEqual({command.name for command in commands}, {
                "help", "ping",
            })
            self.assertTrue(bot.intents.members)
            self.assertTrue(bot.intents.message_content)
            self.assertTrue(bot.intents.guild_messages)
            self.assertFalse(bot.intents.voice_states)
            self.assertIsNone(bot.get_command("play"))
            self.assertEqual({cog for cog in bot.cogs}, {"Membership"})
        self.assertTrue(bot.is_closed())


if __name__ == "__main__":
    unittest.main()
