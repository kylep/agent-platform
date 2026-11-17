from enum import Enum


class UiPresenceSessionState(str, Enum):
    EDITING = "editing"
    VIEWING = "viewing"

    def __str__(self) -> str:
        return str(self.value)
