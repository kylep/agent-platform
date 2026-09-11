from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="RelayReactionView")


@_attrs_define
class RelayReactionView:
    """
    Attributes:
        count (int):
        emoji (str):
        mine (bool):
    """

    count: int
    emoji: str
    mine: bool
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        count = self.count

        emoji = self.emoji

        mine = self.mine

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "count": count,
                "emoji": emoji,
                "mine": mine,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        count = d.pop("count")

        emoji = d.pop("emoji")

        mine = d.pop("mine")

        relay_reaction_view = cls(
            count=count,
            emoji=emoji,
            mine=mine,
        )

        relay_reaction_view.additional_properties = d
        return relay_reaction_view

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
