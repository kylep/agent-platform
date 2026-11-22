from enum import Enum


class UiCommentNotificationStatus(str, Enum):
    QUEUED = "queued"
    RUN_CREATED = "run_created"
    SKIPPED = "skipped"

    def __str__(self) -> str:
        return str(self.value)
