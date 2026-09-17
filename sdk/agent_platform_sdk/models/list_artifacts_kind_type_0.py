from enum import Enum


class ListArtifactsKindType0(str, Enum):
    FILE = "file"
    IMAGE = "image"

    def __str__(self) -> str:
        return str(self.value)
