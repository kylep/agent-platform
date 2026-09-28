from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="WikiAppendIn")


@_attrs_define
class WikiAppendIn:
    """
    Attributes:
        body (str):
        reason (str | Unset):  Default: ''.
    """

    body: str
    reason: str | Unset = ""

    def to_dict(self) -> dict[str, Any]:
        body = self.body

        reason = self.reason

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "body": body,
            }
        )
        if reason is not UNSET:
            field_dict["reason"] = reason

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        body = d.pop("body")

        reason = d.pop("reason", UNSET)

        wiki_append_in = cls(
            body=body,
            reason=reason,
        )

        return wiki_append_in
