from enum import Enum


class GuardInLevelType0(str, Enum):
    LOCK = "lock"
    PIN = "pin"

    def __str__(self) -> str:
        return str(self.value)
