from enum import Enum


class UiMotionExportInFormat(str, Enum):
    GIF = "gif"
    MP4 = "mp4"

    def __str__(self) -> str:
        return str(self.value)
