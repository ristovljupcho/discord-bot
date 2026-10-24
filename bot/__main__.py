import asyncio
import logging
import os
import signal

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import Settings
from bot.membership import Membership

log = logging.getLogger(__name__)


class CommandTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id != self.client.settings.guild_id:
            await interaction.response.send_message("This bot is configured for another server.", ephemeral=True)
            return False
        return True

    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        original = getattr(error, "original", error)
        log.error("Slash command failed (%s).", type(original).__name__)
        message = "The command failed. Check the bot logs and permissions, then try again."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            log.warning("Could not deliver a command error response.")


class PersonalBot(commands.Bot):
    def __init__(self, settings: Settings):
        intents = discord.Intents(
            guilds=True, members=True,
            guild_messages=True, message_content=True,
        )
        super().__init__(
            command_prefix=settings.command_prefix, help_command=None,
            intents=intents, tree_cls=CommandTree,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.settings = settings
        self.add_check(self.check_server)

    async def check_server(self, ctx: commands.Context) -> bool:
        if ctx.guild is None or ctx.guild.id != self.settings.guild_id:
            raise commands.CheckFailure("Use commands in the configured server.")
        return True

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.CommandNotFound):
            return  # Ignore other bots' commands and unknown names.
        original = getattr(error, "original", error)
        while isinstance(original, (commands.CommandInvokeError, commands.HybridCommandError, app_commands.CommandInvokeError)):
            original = original.original
        if isinstance(error, commands.UserInputError):
            command = ctx.command
            message = f"Usage: `{self.command_prefix}{command.qualified_name} {command.signature}`"
        elif isinstance(error, commands.CheckFailure):
            message = "Use commands in the configured server."
        elif isinstance(original, discord.Forbidden):
            message = "I need permission to view and send messages in this channel."
        else:
            log.error("Command failed (%s).", type(original).__name__)
            message = "The command failed. Check the bot logs and permissions, then try again."
        try:
            await ctx.send(message, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            log.warning("Could not deliver a command error response.")

    async def setup_hook(self) -> None:
        await self.add_cog(Membership(self.settings))

        @self.hybrid_command(name="ping", description="Check that the bot is responding.")
        async def ping(ctx: commands.Context) -> None:
            await ctx.send(f"Pong! Gateway latency: {self.latency * 1000:.0f} ms.")

        @self.hybrid_command(name="help", description="List all available commands and their usage.")
        async def help_command(ctx: commands.Context) -> None:
            prefix = self.settings.command_prefix
            lines = ["**Available commands**"]
            for command in sorted(self.walk_commands(), key=lambda command: command.qualified_name):
                usage = f"{prefix}{command.qualified_name} {command.signature}".rstrip()
                lines.append(f"`{usage}` — {command.description}")
            lines.append("\nThese commands also work with `/`.")
            await ctx.send("\n".join(lines), allowed_mentions=discord.AllowedMentions.none())

        guild = discord.Object(id=self.settings.guild_id)
        self.tree.copy_global_to(guild=guild)
        synced = await self.tree.sync(guild=guild)
        log.info("Registered %s commands for server %s.", len(synced), guild.id)

    async def on_ready(self) -> None:
        guild = self.get_guild(self.settings.guild_id)
        if guild is None:
            log.error("Configured server is unavailable. Check GUILD_ID and the bot invitation.")
            return
        log.info("Connected as %s in server %s.", self.user, guild.id)
        if not guild.me.guild_permissions.manage_roles:
            log.error("Manage Roles is missing; default roles cannot be assigned.")
        for role_id in self.settings.default_role_ids:
            role = guild.get_role(role_id)
            if role is None or role.managed or role.is_default() or role >= guild.me.top_role:
                log.error("Default role %s is unavailable or cannot be assigned by this bot.", role_id)
        for channel_id in (self.settings.welcome_channel_id, self.settings.goodbye_channel_id):
            channel = guild.get_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                log.error("Configured text channel %s was not found.", channel_id)
            else:
                permissions = channel.permissions_for(guild.me)
                if not permissions.view_channel or not permissions.send_messages:
                    log.error("View Channel or Send Messages is missing in channel %s.", channel_id)


async def run(settings: Settings) -> None:
    async with PersonalBot(settings) as bot:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda: asyncio.create_task(bot.close()))
            except NotImplementedError:
                pass  # Windows uses asyncio.run's KeyboardInterrupt handling.
        await bot.start(settings.token)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        settings = Settings.from_env(os.environ)
        asyncio.run(run(settings))
    except KeyboardInterrupt:
        pass
    except (ValueError, RuntimeError, discord.DiscordException) as exc:
        # Do not print exception bodies from Discord, which can include request data.
        if isinstance(exc, (ValueError, RuntimeError)):
            log.error("Startup failed: %s", exc)
        elif isinstance(exc, discord.PrivilegedIntentsRequired):
            log.error(
                "Enable Server Members Intent AND Message Content Intent in Developer Portal > Bot > "
                "Privileged Gateway Intents, save, then restart the bot."
            )
        else:
            log.error("Discord startup failed (%s). Check the token, intents, server ID, and invitation.", type(exc).__name__)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
