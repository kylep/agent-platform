from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="WikiAppendIn")


@_attrs_define
class WikiAppendIn:
    """
    Attributes:
        body (str):
        parent (None | str | Unset):
        reason (str | Unset):  Default: ''.
    """

    body: str
    parent: None | str | Unset = UNSET
    reason: str | Unset = ""

    def to_dict(self) -> dict[str, Any]:
        body = self.body

        parent: None | str | Unset
        if isinstance(self.parent, Unset):
            parent = UNSET
        else:
            parent = self.parent

        reason = self.reason

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "body": body,
            }
        )
        if parent is not UNSET:
            field_dict["parent"] = parent
        if reason is not UNSET:
            field_dict["reason"] = reason

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        body = d.pop("body")

        def _parse_parent(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        parent = _parse_parent(d.pop("parent", UNSET))

        reason = d.pop("reason", UNSET)

        wiki_append_in = cls(
            body=body,
            parent=parent,
            reason=reason,
        )

        return wiki_append_in
