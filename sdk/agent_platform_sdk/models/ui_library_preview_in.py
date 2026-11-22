from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="UiLibraryPreviewIn")


@_attrs_define
class UiLibraryPreviewIn:
    """
    Attributes:
        source_version (int):
    """

    source_version: int

    def to_dict(self) -> dict[str, Any]:
        source_version = self.source_version

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "source_version": source_version,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        source_version = d.pop("source_version")

        ui_library_preview_in = cls(
            source_version=source_version,
        )

        return ui_library_preview_in
