from enum import Enum


class ImageModelBilling(str, Enum):
    API = "api"
    CODEX = "codex"

    def __str__(self) -> str:
        return str(self.value)
