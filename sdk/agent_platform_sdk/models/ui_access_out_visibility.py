from enum import Enum


class UiAccessOutVisibility(str, Enum):
    RESTRICTED = "restricted"
    WORKSPACE = "workspace"

    def __str__(self) -> str:
        return str(self.value)
