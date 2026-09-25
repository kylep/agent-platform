from enum import Enum


class TypedBlockKind(str, Enum):
    ACTION = "action"
    CHAT = "chat"
    HEADING = "heading"
    LINK = "link"
    METRIC = "metric"
    PARAGRAPH = "paragraph"
    TABLE = "table"

    def __str__(self) -> str:
        return str(self.value)
