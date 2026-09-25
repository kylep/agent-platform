from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from typing_extensions import Self

T = TypeVar("T", bound="ActionBinding")


@_attrs_define
class ActionBinding:
    """
    Attributes:
        alias (str):
        channel (str):
        operation (str):
    """

    alias: str
    channel: str
    operation: str

    def to_dict(self) -> dict[str, Any]:
        alias = self.alias

        channel = self.channel

        operation = self.operation

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "alias": alias,
                "channel": channel,
                "operation": operation,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        alias = d.pop("alias")

        channel = d.pop("channel")

        operation = d.pop("operation")

        action_binding = cls(
            alias=alias,
            channel=channel,
            operation=operation,
        )

        return action_binding
