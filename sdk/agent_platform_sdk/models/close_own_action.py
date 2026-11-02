from enum import Enum


class CloseOwnAction(str, Enum):
    COMPLETE = "complete"
    VOID = "void"

    def __str__(self) -> str:
        return str(self.value)
