from enum import Enum


class RefreshQuotaProvider(str, Enum):
    ALL = "all"
    CLAUDE = "claude"
    CODEX = "codex"

    def __str__(self) -> str:
        return str(self.value)
