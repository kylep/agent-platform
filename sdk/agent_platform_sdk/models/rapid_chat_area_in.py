from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="RapidChatAreaIn")


@_attrs_define
class RapidChatAreaIn:
    """A launcher-supplied area; `seed` starts it `drafted` so it is critiqued,
    not re-derived.

        Attributes:
            title (str):
            seed (None | str | Unset):
    """

    title: str
    seed: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        title = self.title

        seed: None | str | Unset
        if isinstance(self.seed, Unset):
            seed = UNSET
        else:
            seed = self.seed

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "title": title,
            }
        )
        if seed is not UNSET:
            field_dict["seed"] = seed

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        title = d.pop("title")

        def _parse_seed(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        seed = _parse_seed(d.pop("seed", UNSET))

        rapid_chat_area_in = cls(
            title=title,
            seed=seed,
        )

        return rapid_chat_area_in
