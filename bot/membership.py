import logging

import discord
from discord.ext import commands

from bot.config import Settings

log = logging.getLogger(__name__)


class Membership(commands.Cog):
    def __init__(self, settings: Settings):
        self.settings = settings

    async def assign_roles(self, member: discord.Member) -> None:
        if member.guild.id != self.settings.guild_id or member.bot or member.pending:
            return
        me = member.guild.me
        if me is None or not me.guild_permissions.manage_roles:
            log.error("Cannot assign default roles: Manage Roles permission is missing.")
            return
        roles = []
        for role_id in self.settings.default_role_ids:
            role = member.guild.get_role(role_id)
            if role is None or role.is_default() or role.managed or role >= me.top_role:
                log.error("Default role %s is missing, managed, or above the bot role.", role_id)
            elif role not in member.roles:
                roles.append(role)
        if roles:
            try:
                await member.add_roles(*roles, reason="Configured default membership roles")
            except discord.HTTPException:
                log.error("Discord rejected default role assignment for member %s.", member.id)

    async def announce(self, member: discord.Member, *, joining: bool) -> None:
        channel_id = (
            self.settings.welcome_channel_id if joining else self.settings.goodbye_channel_id
        )
        channel = member.guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            log.error("Announcement channel %s is missing or is not a text channel.", channel_id)
            return
        template = self.settings.welcome_message if joining else self.settings.goodbye_message
        content = template.format(
            mention=member.mention,
            name=discord.utils.escape_markdown(member.display_name),
            server=discord.utils.escape_markdown(member.guild.name),
        )[:2000]
        try:
            await channel.send(
                content,
                allowed_mentions=discord.AllowedMentions(
                    everyone=False, roles=False, users=[member] if joining else False,
                ),
            )
        except discord.HTTPException:
            log.error("Discord rejected the announcement in channel %s.", channel_id)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.guild.id == self.settings.guild_id and not member.bot:
            await self.assign_roles(member)
            await self.announce(member, joining=True)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if before.pending and not after.pending:
            await self.assign_roles(after)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if member.guild.id == self.settings.guild_id and not member.bot:
            await self.announce(member, joining=False)
