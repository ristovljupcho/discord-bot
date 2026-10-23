from dataclasses import dataclass
from string import Formatter
from typing import Mapping


@dataclass(frozen=True)
class Settings:
    token: str
    guild_id: int
    default_role_ids: tuple[int, ...]
    welcome_channel_id: int
    goodbye_channel_id: int
    welcome_message: str = "Welcome {mention} to {server}!"
    goodbye_message: str = "{name} has left {server}."
    command_prefix: str = "~"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Settings":
        token = env.get("DISCORD_TOKEN", "").strip()
        if not token or token == "paste-your-bot-token-here":
            raise ValueError("Set DISCORD_TOKEN to your bot token.")
        prefix = env.get("COMMAND_PREFIX", cls.command_prefix)
        if not 1 <= len(prefix) <= 10 or any(c.isspace() for c in prefix) or any(c in prefix for c in "/`"):
            raise ValueError("COMMAND_PREFIX must be 1-10 characters without whitespace, /, or backticks.")

        def snowflake(name: str, value: str | None = None) -> int:
            raw = env.get(name, "") if value is None else value
            try:
                result = int(raw)
            except ValueError:
                raise ValueError(f"{name} must contain a positive Discord ID.") from None
            if not 0 < result < 2**64:
                raise ValueError(f"{name} must contain a positive Discord ID.")
            return result

        roles = env.get("DEFAULT_ROLE_IDS", "").strip()
        if not roles:
            raise ValueError("Set DEFAULT_ROLE_IDS to comma-separated role IDs.")
        values = {
            "welcome_message": env.get("WELCOME_MESSAGE", cls.welcome_message),
            "goodbye_message": env.get("GOODBYE_MESSAGE", cls.goodbye_message),
        }
        for name, template in values.items():
            if not template or len(template) > 1500:
                raise ValueError(f"{name} must contain between 1 and 1500 characters.")
            try:
                for _, field, spec, conversion in Formatter().parse(template):
                    if field is not None and (
                        field not in {"mention", "name", "server"} or spec or conversion
                    ):
                        raise ValueError("Unsupported template field")
            except ValueError:
                raise ValueError(
                    f"{name} supports only {{mention}}, {{name}}, and {{server}}."
                ) from None
        return cls(
            token=token,
            guild_id=snowflake("GUILD_ID"),
            default_role_ids=tuple(dict.fromkeys(
                snowflake("DEFAULT_ROLE_IDS", item.strip()) for item in roles.split(",")
            )),
            welcome_channel_id=snowflake("WELCOME_CHANNEL_ID"),
            goodbye_channel_id=snowflake("GOODBYE_CHANNEL_ID"),
            command_prefix=prefix,
            **values,
        )
