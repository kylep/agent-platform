from enum import Enum


class RapidChatSinkInKind(str, Enum):
    ARTIFACT = "artifact"
    RELAY = "relay"
    UI_DESIGNER = "ui_designer"
    WIKI = "wiki"

    def __str__(self) -> str:
        return str(self.value)
