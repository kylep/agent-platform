from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

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
            external_ref (str):
    """

    channel_id: str
    config: RelayBindingRefConfig
    external_ref: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        channel_id = self.channel_id

        config = self.config.to_dict()

        external_ref = self.external_ref

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "channel_id": channel_id,
                "config": config,
                "external_ref": external_ref,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.relay_binding_ref_config import RelayBindingRefConfig

        d = dict(src_dict)
        channel_id = d.pop("channel_id")

        config = RelayBindingRefConfig.from_dict(d.pop("config"))

        external_ref = d.pop("external_ref")

        relay_binding_ref = cls(
            channel_id=channel_id,
            config=config,
            external_ref=external_ref,
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
