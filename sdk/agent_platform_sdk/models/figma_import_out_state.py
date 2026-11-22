from enum import Enum


class FigmaImportOutState(str, Enum):
    DONE = "done"
    FAILED = "failed"
    QUEUED = "queued"
    RUNNING = "running"

    def __str__(self) -> str:
        return str(self.value)
