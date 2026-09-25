from enum import Enum


class RelayChannelPatchReplyModeType0(str, Enum):
    LINEAR = "linear"
    THREADED = "threaded"

    def __str__(self) -> str:
        return str(self.value)
