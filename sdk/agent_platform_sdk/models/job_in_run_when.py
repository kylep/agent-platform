from enum import Enum


class JobInRunWhen(str, Enum):
    ALWAYS = "always"
    DISCORD_UNADDRESSED = "discord_unaddressed"

    def __str__(self) -> str:
        return str(self.value)
