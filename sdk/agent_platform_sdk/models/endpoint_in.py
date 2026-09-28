from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="EndpointIn")


@_attrs_define
class EndpointIn:
    """
    Attributes:
        external_ref (str):
        can_history (bool | Unset):  Default: False.
        can_read (bool | Unset):  Default: False.
        can_send (bool | Unset):  Default: False.
        display_name (str | Unset):  Default: ''.
        kind (str | Unset):  Default: 'channel'.
    """

    external_ref: str
    can_history: bool | Unset = False
    can_read: bool | Unset = False
    can_send: bool | Unset = False
    display_name: str | Unset = ""
    kind: str | Unset = "channel"
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        external_ref = self.external_ref

        can_history = self.can_history

        can_read = self.can_read

        can_send = self.can_send

        display_name = self.display_name

        kind = self.kind

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "external_ref": external_ref,
            }
        )
        if can_history is not UNSET:
            field_dict["can_history"] = can_history
        if can_read is not UNSET:
            field_dict["can_read"] = can_read
        if can_send is not UNSET:
            field_dict["can_send"] = can_send
        if display_name is not UNSET:
            field_dict["display_name"] = display_name
        if kind is not UNSET:
            field_dict["kind"] = kind

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        external_ref = d.pop("external_ref")

        can_history = d.pop("can_history", UNSET)

        can_read = d.pop("can_read", UNSET)

        can_send = d.pop("can_send", UNSET)

        display_name = d.pop("display_name", UNSET)

        kind = d.pop("kind", UNSET)

        endpoint_in = cls(
            external_ref=external_ref,
            can_history=can_history,
            can_read=can_read,
            can_send=can_send,
            display_name=display_name,
            kind=kind,
        )

        endpoint_in.additional_properties = d
        return endpoint_in

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
