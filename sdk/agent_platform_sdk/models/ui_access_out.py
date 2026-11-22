from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.ui_access_out_visibility import UiAccessOutVisibility
from ..types import UNSET, Unset

T = TypeVar("T", bound="UiAccessOut")


@_attrs_define
class UiAccessOut:
    """
    Attributes:
        access_version (int):
        can_manage_sharing (bool):
        can_read (bool):
        can_write (bool):
        document_id (str):
        editors (list[str]):
        owner (str):
        readers (list[str]):
        visibility (UiAccessOutVisibility):
        replayed (bool | Unset):  Default: False.
    """

    access_version: int
    can_manage_sharing: bool
    can_read: bool
    can_write: bool
    document_id: str
    editors: list[str]
    owner: str
    readers: list[str]
    visibility: UiAccessOutVisibility
    replayed: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        access_version = self.access_version

        can_manage_sharing = self.can_manage_sharing

        can_read = self.can_read

        can_write = self.can_write

        document_id = self.document_id

        editors = self.editors

        owner = self.owner

        readers = self.readers

        visibility = self.visibility.value

        replayed = self.replayed

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "access_version": access_version,
                "can_manage_sharing": can_manage_sharing,
                "can_read": can_read,
                "can_write": can_write,
                "document_id": document_id,
                "editors": editors,
                "owner": owner,
                "readers": readers,
                "visibility": visibility,
            }
        )
        if replayed is not UNSET:
            field_dict["replayed"] = replayed

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        access_version = d.pop("access_version")

        can_manage_sharing = d.pop("can_manage_sharing")

        can_read = d.pop("can_read")

        can_write = d.pop("can_write")

        document_id = d.pop("document_id")

        editors = cast(list[str], d.pop("editors"))

        owner = d.pop("owner")

        readers = cast(list[str], d.pop("readers"))

        visibility = UiAccessOutVisibility(d.pop("visibility"))

        replayed = d.pop("replayed", UNSET)

        ui_access_out = cls(
            access_version=access_version,
            can_manage_sharing=can_manage_sharing,
            can_read=can_read,
            can_write=can_write,
            document_id=document_id,
            editors=editors,
            owner=owner,
            readers=readers,
            visibility=visibility,
            replayed=replayed,
        )

        ui_access_out.additional_properties = d
        return ui_access_out

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
