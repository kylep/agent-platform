from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.relay_binding_view_config import RelayBindingViewConfig


T = TypeVar("T", bound="RelayBindingView")


@_attrs_define
class RelayBindingView:
    """A room on another network that mirrors this channel (docs/design/19).
    `external_ref` is that network's own id for it — a Discord channel or
    thread snowflake — and is unique per connector platform-wide.

        Attributes:
            config (RelayBindingViewConfig):
            connector (str):
            external_ref (str):
            id (str):
    """

    config: RelayBindingViewConfig
    connector: str
    external_ref: str
    id: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        config = self.config.to_dict()

        connector = self.connector

        external_ref = self.external_ref

        id = self.id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "config": config,
                "connector": connector,
                "external_ref": external_ref,
                "id": id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.relay_binding_view_config import RelayBindingViewConfig

        d = dict(src_dict)
        config = RelayBindingViewConfig.from_dict(d.pop("config"))

        connector = d.pop("connector")

        external_ref = d.pop("external_ref")

        id = d.pop("id")

        relay_binding_view = cls(
            config=config,
            connector=connector,
            external_ref=external_ref,
            id=id,
        )

        relay_binding_view.additional_properties = d
        return relay_binding_view

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
