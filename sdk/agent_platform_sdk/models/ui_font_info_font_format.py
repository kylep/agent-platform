from enum import Enum


class UiFontInfoFontFormat(str, Enum):
    OTF = "otf"
    TTF = "ttf"

    def __str__(self) -> str:
        return str(self.value)
