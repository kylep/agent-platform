from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="WikiMoveIn")


@_attrs_define
class WikiMoveIn:
    """File a page under another one. `parent` is that page's slug, and ""
    is the top level — required either way, because a move that does not say
    where to is not a move.

        Attributes:
            parent (str):
            reason (str | Unset):  Default: ''.
    """

    parent: str
    reason: str | Unset = ""

    def to_dict(self) -> dict[str, Any]:
        parent = self.parent

        reason = self.reason

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "parent": parent,
            }
        )
        if reason is not UNSET:
            field_dict["reason"] = reason

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        parent = d.pop("parent")

        reason = d.pop("reason", UNSET)

        wiki_move_in = cls(
            parent=parent,
            reason=reason,
        )

        return wiki_move_in
