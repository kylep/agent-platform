from enum import Enum


class ListArtifactsSourceType0(str, Enum):
    DERIVED = "derived"
    GENERATED = "generated"
    TOOL = "tool"
    UPLOAD = "upload"

    def __str__(self) -> str:
        return str(self.value)
