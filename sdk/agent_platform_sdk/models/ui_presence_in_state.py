from enum import Enum


class UiPresenceInState(str, Enum):
    EDITING = "editing"
    VIEWING = "viewing"

    def __str__(self) -> str:
        return str(self.value)
