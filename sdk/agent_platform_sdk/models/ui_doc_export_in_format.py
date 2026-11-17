from enum import Enum


class UiDocExportInFormat(str, Enum):
    HTML = "html"
    PDF = "pdf"
    PNG = "png"
    SVG = "svg"

    def __str__(self) -> str:
        return str(self.value)
