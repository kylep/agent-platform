from enum import Enum


class UiAccessInVisibility(str, Enum):
    RESTRICTED = "restricted"
    WORKSPACE = "workspace"

    def __str__(self) -> str:
        return str(self.value)
