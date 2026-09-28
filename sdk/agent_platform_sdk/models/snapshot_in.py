from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.endpoint_in import EndpointIn


T = TypeVar("T", bound="SnapshotIn")


@_attrs_define
class SnapshotIn:
    """
    Attributes:
        endpoints (list[EndpointIn]):
        ownership_generation (int):
        sequence (int):
    """

    endpoints: list[EndpointIn]
    ownership_generation: int
    sequence: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        endpoints = []
        for endpoints_item_data in self.endpoints:
            endpoints_item = endpoints_item_data.to_dict()
            endpoints.append(endpoints_item)

        ownership_generation = self.ownership_generation

        sequence = self.sequence

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "endpoints": endpoints,
                "ownership_generation": ownership_generation,
                "sequence": sequence,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.endpoint_in import EndpointIn

        d = dict(src_dict)
        endpoints = []
        _endpoints = d.pop("endpoints")
        for endpoints_item_data in _endpoints:
            endpoints_item = EndpointIn.from_dict(endpoints_item_data)

            endpoints.append(endpoints_item)

        ownership_generation = d.pop("ownership_generation")

        sequence = d.pop("sequence")

        snapshot_in = cls(
            endpoints=endpoints,
            ownership_generation=ownership_generation,
            sequence=sequence,
        )

        snapshot_in.additional_properties = d
        return snapshot_in

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
