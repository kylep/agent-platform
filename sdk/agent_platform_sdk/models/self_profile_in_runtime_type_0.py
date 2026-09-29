from enum import Enum


class SelfProfileInRuntimeType0(str, Enum):
    CLAUDE = "claude"
    CODEX = "codex"

    def __str__(self) -> str:
        return str(self.value)
