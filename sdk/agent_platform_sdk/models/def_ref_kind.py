from enum import Enum


class DefRefKind(str, Enum):
    COLLECTION = "collection"
    PAGE = "page"
    TOOL = "tool"
    VIEW = "view"

    def __str__(self) -> str:
        return str(self.value)
