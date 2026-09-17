from enum import Enum


class CreateArtifactJsonArtifactInSource(str, Enum):
    DERIVED = "derived"
    TOOL = "tool"
    UPLOAD = "upload"

    def __str__(self) -> str:
        return str(self.value)
