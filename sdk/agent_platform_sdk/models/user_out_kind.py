from enum import Enum


class UserOutKind(str, Enum):
    STATE = "state"
    SYSTEM = "system"

    def __str__(self) -> str:
        return str(self.value)
