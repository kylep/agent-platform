from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="UiLibraryApplyIn")


@_attrs_define
class UiLibraryApplyIn:
    """
    Attributes:
        expected_version (int):
        request_id (str):
        source_version (int):
    """

    expected_version: int
    request_id: str
    source_version: int

    def to_dict(self) -> dict[str, Any]:
        expected_version = self.expected_version

        request_id = self.request_id

        source_version = self.source_version

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "expected_version": expected_version,
                "request_id": request_id,
                "source_version": source_version,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        expected_version = d.pop("expected_version")

        request_id = d.pop("request_id")

        source_version = d.pop("source_version")

        ui_library_apply_in = cls(
            expected_version=expected_version,
            request_id=request_id,
            source_version=source_version,
        )

        return ui_library_apply_in
