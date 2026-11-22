from enum import Enum


class UiMotionPreviewOutEasing(str, Enum):
    EASE_IN = "ease-in"
    EASE_IN_OUT = "ease-in-out"
    EASE_OUT = "ease-out"
    LINEAR = "linear"

    def __str__(self) -> str:
        return str(self.value)
