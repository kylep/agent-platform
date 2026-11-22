from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.ui_access_in_visibility import UiAccessInVisibility
from ..types import UNSET, Unset

T = TypeVar("T", bound="UiAccessIn")


@_attrs_define
class UiAccessIn:
    """
    Attributes:
        expected_access_version (int):
        request_id (str):
        visibility (UiAccessInVisibility):
        editors (list[str] | Unset):
        readers (list[str] | Unset):
    """

    expected_access_version: int
    request_id: str
    visibility: UiAccessInVisibility
    editors: list[str] | Unset = UNSET
    readers: list[str] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        expected_access_version = self.expected_access_version

        request_id = self.request_id

        visibility = self.visibility.value

        editors: list[str] | Unset = UNSET
        if not isinstance(self.editors, Unset):
            editors = self.editors

        readers: list[str] | Unset = UNSET
        if not isinstance(self.readers, Unset):
            readers = self.readers

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "expected_access_version": expected_access_version,
                "request_id": request_id,
                "visibility": visibility,
            }
        )
        if editors is not UNSET:
            field_dict["editors"] = editors
        if readers is not UNSET:
            field_dict["readers"] = readers

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        expected_access_version = d.pop("expected_access_version")

        request_id = d.pop("request_id")

        visibility = UiAccessInVisibility(d.pop("visibility"))

        editors = cast(list[str], d.pop("editors", UNSET))

        readers = cast(list[str], d.pop("readers", UNSET))

        ui_access_in = cls(
            expected_access_version=expected_access_version,
            request_id=request_id,
            visibility=visibility,
            editors=editors,
            readers=readers,
        )

        return ui_access_in
