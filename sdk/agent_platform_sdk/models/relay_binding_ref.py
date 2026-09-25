from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.relay_binding_ref_config import RelayBindingRefConfig


T = TypeVar("T", bound="RelayBindingRef")


@_attrs_define
class RelayBindingRef:
    """The cross-channel listing a connector reads at startup: which of its
    rooms map to which channel. The connector is the query, so it is not
    repeated on every row.

        Attributes:
            channel_id (str):
            config (RelayBindingRefConfig):
            external_kind (str):
            external_ref (str):
            display_name (str | Unset):  Default: ''.
            external_url (str | Unset):  Default: ''.
            parent_external_ref (None | str | Unset):
            status (str | Unset):  Default: 'active'.
    """

    channel_id: str
    config: RelayBindingRefConfig
    external_kind: str
    external_ref: str
    display_name: str | Unset = ""
    external_url: str | Unset = ""
    parent_external_ref: None | str | Unset = UNSET
    status: str | Unset = "active"
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        channel_id = self.channel_id

        config = self.config.to_dict()

        external_kind = self.external_kind

        external_ref = self.external_ref

        display_name = self.display_name

        external_url = self.external_url

        parent_external_ref: None | str | Unset
        if isinstance(self.parent_external_ref, Unset):
            parent_external_ref = UNSET
        else:
            parent_external_ref = self.parent_external_ref

        status = self.status

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "channel_id": channel_id,
                "config": config,
                "external_kind": external_kind,
                "external_ref": external_ref,
            }
        )
        if display_name is not UNSET:
            field_dict["display_name"] = display_name
        if external_url is not UNSET:
            field_dict["external_url"] = external_url
        if parent_external_ref is not UNSET:
            field_dict["parent_external_ref"] = parent_external_ref
        if status is not UNSET:
            field_dict["status"] = status

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.relay_binding_ref_config import RelayBindingRefConfig

        d = dict(src_dict)
        channel_id = d.pop("channel_id")

        config = RelayBindingRefConfig.from_dict(d.pop("config"))

        external_kind = d.pop("external_kind")

        external_ref = d.pop("external_ref")

        display_name = d.pop("display_name", UNSET)

        external_url = d.pop("external_url", UNSET)

        def _parse_parent_external_ref(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        parent_external_ref = _parse_parent_external_ref(
            d.pop("parent_external_ref", UNSET)
        )

        status = d.pop("status", UNSET)

        relay_binding_ref = cls(
            channel_id=channel_id,
            config=config,
            external_kind=external_kind,
            external_ref=external_ref,
            display_name=display_name,
            external_url=external_url,
            parent_external_ref=parent_external_ref,
            status=status,
        )

        relay_binding_ref.additional_properties = d
        return relay_binding_ref

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
