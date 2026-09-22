from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="TicketCommentIn")


@_attrs_define
class TicketCommentIn:
    """
    Attributes:
        body (str):
    """

    body: str

    def to_dict(self) -> dict[str, Any]:
        body = self.body

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "body": body,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        body = d.pop("body")

        ticket_comment_in = cls(
            body=body,
        )

        return ticket_comment_in
