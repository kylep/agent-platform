from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="UiDocRestoreIn")


@_attrs_define
class UiDocRestoreIn:
    """
    Attributes:
        expected_version (int):
        version (int):
        reason (str | Unset):  Default: ''.
        request_id (None | str | Unset):
    """

    expected_version: int
    version: int
    reason: str | Unset = ""
    request_id: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        expected_version = self.expected_version

        version = self.version

        reason = self.reason

        request_id: None | str | Unset
        if isinstance(self.request_id, Unset):
            request_id = UNSET
        else:
            request_id = self.request_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "expected_version": expected_version,
                "version": version,
            }
        )
        if reason is not UNSET:
            field_dict["reason"] = reason
        if request_id is not UNSET:
            field_dict["request_id"] = request_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        expected_version = d.pop("expected_version")

        version = d.pop("version")

        reason = d.pop("reason", UNSET)

        def _parse_request_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        request_id = _parse_request_id(d.pop("request_id", UNSET))

        ui_doc_restore_in = cls(
            expected_version=expected_version,
            version=version,
            reason=reason,
            request_id=request_id,
        )

        ui_doc_restore_in.additional_properties = d
        return ui_doc_restore_in

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
