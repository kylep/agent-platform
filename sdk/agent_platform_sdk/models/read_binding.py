from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="ReadBinding")


@_attrs_define
class ReadBinding:
    """
    Attributes:
        alias (str):
        operation (str):
        channel_id (None | str | Unset):
    """

    alias: str
    operation: str
    channel_id: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        alias = self.alias

        operation = self.operation

        channel_id: None | str | Unset
        if isinstance(self.channel_id, Unset):
            channel_id = UNSET
        else:
            channel_id = self.channel_id

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "alias": alias,
                "operation": operation,
            }
        )
        if channel_id is not UNSET:
            field_dict["channel_id"] = channel_id

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        alias = d.pop("alias")

        operation = d.pop("operation")

        def _parse_channel_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        channel_id = _parse_channel_id(d.pop("channel_id", UNSET))

        read_binding = cls(
            alias=alias,
            operation=operation,
            channel_id=channel_id,
        )

        return read_binding
