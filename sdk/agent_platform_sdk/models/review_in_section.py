from enum import Enum


class ReviewInSection(str, Enum):
    COMMITMENTS = "commitments"
    CONVERSATIONS = "conversations"
    MEMORIES = "memories"

    def __str__(self) -> str:
        return str(self.value)
