from enum import IntEnum


class UiMotionExportInFps(IntEnum):
    VALUE_12 = 12
    VALUE_24 = 24

    def __str__(self) -> str:
        return str(self.value)
