from __future__ import annotations

from collections.abc import Mapping
from typing import (
    Any,
    Literal,
    TypeVar,
    cast,
)

from attrs import define as _attrs_define

from ..models.ui_font_info_font_format import UiFontInfoFontFormat
from ..types import UNSET, Unset

T = TypeVar("T", bound="UiFontInfo")


@_attrs_define
class UiFontInfo:
    """
    Attributes:
        family (str):
        font_format (UiFontInfoFontFormat):
        glyphs (int):
        license_ (Literal['OFL-1.1']):
        license_text (str):
        sha256 (str):
        units_per_em (int):
        weight (int):
        copyright_ (str | Unset):  Default: ''.
        license_url (str | Unset):  Default: ''.
    """

    family: str
    font_format: UiFontInfoFontFormat
    glyphs: int
    license_: Literal["OFL-1.1"]
    license_text: str
    sha256: str
    units_per_em: int
    weight: int
    copyright_: str | Unset = ""
    license_url: str | Unset = ""

    def to_dict(self) -> dict[str, Any]:
        family = self.family

        font_format = self.font_format.value

        glyphs = self.glyphs

        license_ = self.license_

        license_text = self.license_text

        sha256 = self.sha256

        units_per_em = self.units_per_em

        weight = self.weight

        copyright_ = self.copyright_

        license_url = self.license_url

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "family": family,
                "font_format": font_format,
                "glyphs": glyphs,
                "license": license_,
                "license_text": license_text,
                "sha256": sha256,
                "units_per_em": units_per_em,
                "weight": weight,
            }
        )
        if copyright_ is not UNSET:
            field_dict["copyright"] = copyright_
        if license_url is not UNSET:
            field_dict["license_url"] = license_url

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        family = d.pop("family")

        font_format = UiFontInfoFontFormat(d.pop("font_format"))

        glyphs = d.pop("glyphs")

        license_ = cast(Literal["OFL-1.1"], d.pop("license"))
        if license_ != "OFL-1.1":
            raise ValueError(f"license must match const 'OFL-1.1', got '{license_}'")

        license_text = d.pop("license_text")

        sha256 = d.pop("sha256")

        units_per_em = d.pop("units_per_em")

        weight = d.pop("weight")

        copyright_ = d.pop("copyright", UNSET)

        license_url = d.pop("license_url", UNSET)

        ui_font_info = cls(
            family=family,
            font_format=font_format,
            glyphs=glyphs,
            license_=license_,
            license_text=license_text,
            sha256=sha256,
            units_per_em=units_per_em,
            weight=weight,
            copyright_=copyright_,
            license_url=license_url,
        )

        return ui_font_info
