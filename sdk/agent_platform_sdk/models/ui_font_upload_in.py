from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="UiFontUploadIn")


@_attrs_define
class UiFontUploadIn:
    """
    Attributes:
        data_base64 (str):
        expected_version (int):
        request_id (str):
    """

    data_base64: str
    expected_version: int
    request_id: str

    def to_dict(self) -> dict[str, Any]:
        data_base64 = self.data_base64

        expected_version = self.expected_version

        request_id = self.request_id

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "data_base64": data_base64,
                "expected_version": expected_version,
                "request_id": request_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        data_base64 = d.pop("data_base64")

        expected_version = d.pop("expected_version")

        request_id = d.pop("request_id")

        ui_font_upload_in = cls(
            data_base64=data_base64,
            expected_version=expected_version,
            request_id=request_id,
        )

        return ui_font_upload_in
