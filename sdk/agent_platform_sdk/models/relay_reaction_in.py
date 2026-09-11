from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from typing_extensions import Self

T = TypeVar("T", bound="RelayReactionIn")


@_attrs_define
class RelayReactionIn:
    """
    Attributes:
        emoji (str):
    """

    emoji: str

    def to_dict(self) -> dict[str, Any]:
        emoji = self.emoji

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "emoji": emoji,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        emoji = d.pop("emoji")

        relay_reaction_in = cls(
            emoji=emoji,
        )

        return relay_reaction_in
