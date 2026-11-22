from enum import Enum


class ListUiPlatformComponentsDetailType0(str, Enum):
    FULL = "full"
    INDEX = "index"

    def __str__(self) -> str:
        return str(self.value)
