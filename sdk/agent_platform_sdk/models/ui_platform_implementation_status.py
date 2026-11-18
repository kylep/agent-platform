from enum import Enum


class UiPlatformImplementationStatus(str, Enum):
    DETACHED = "detached"
    EXACT = "exact"

    def __str__(self) -> str:
        return str(self.value)
