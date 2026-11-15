from enum import Enum


class RapidChatSinkInMode(str, Enum):
    APPEND = "append"
    REPLACE = "replace"

    def __str__(self) -> str:
        return str(self.value)
