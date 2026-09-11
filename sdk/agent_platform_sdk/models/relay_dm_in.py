from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from typing_extensions import Self

T = TypeVar("T", bound="RelayDmIn")


@_attrs_define
class RelayDmIn:
    """
    Attributes:
        with_ (str):
    """

    with_: str

    def to_dict(self) -> dict[str, Any]:
        with_ = self.with_

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "with": with_,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        with_ = d.pop("with")

        relay_dm_in = cls(
            with_=with_,
        )

        return relay_dm_in
