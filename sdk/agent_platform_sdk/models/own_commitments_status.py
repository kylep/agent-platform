from enum import Enum


class OwnCommitmentsStatus(str, Enum):
    ALL = "all"
    DONE = "done"
    OPEN = "open"
    VOID = "void"

    def __str__(self) -> str:
        return str(self.value)
