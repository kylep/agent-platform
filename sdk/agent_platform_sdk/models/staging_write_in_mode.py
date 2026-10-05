from enum import Enum


class StagingWriteInMode(str, Enum):
    INSERT = "insert"
    SKIP_EXISTING = "skip_existing"
    UPSERT = "upsert"

    def __str__(self) -> str:
        return str(self.value)
