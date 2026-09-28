from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="ObservationIn")


@_attrs_define
class ObservationIn:
    """
    Attributes:
        author_id (str):
        external_ref (str):
        ownership_generation (int):
        provider_message_id (str):
        text (str):
        addressed (bool | Unset):  Default: False.
    """

    author_id: str
    external_ref: str
    ownership_generation: int
    provider_message_id: str
    text: str
    addressed: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        author_id = self.author_id

        external_ref = self.external_ref

        ownership_generation = self.ownership_generation

        provider_message_id = self.provider_message_id

        text = self.text

        addressed = self.addressed

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "author_id": author_id,
                "external_ref": external_ref,
                "ownership_generation": ownership_generation,
                "provider_message_id": provider_message_id,
                "text": text,
            }
        )
        if addressed is not UNSET:
            field_dict["addressed"] = addressed

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        author_id = d.pop("author_id")

        external_ref = d.pop("external_ref")

        ownership_generation = d.pop("ownership_generation")

        provider_message_id = d.pop("provider_message_id")

        text = d.pop("text")

        addressed = d.pop("addressed", UNSET)

        observation_in = cls(
            author_id=author_id,
            external_ref=external_ref,
            ownership_generation=ownership_generation,
            provider_message_id=provider_message_id,
            text=text,
            addressed=addressed,
        )

        observation_in.additional_properties = d
        return observation_in

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
