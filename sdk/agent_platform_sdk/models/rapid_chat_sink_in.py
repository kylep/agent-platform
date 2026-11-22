from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

from ..models.rapid_chat_sink_in_kind import RapidChatSinkInKind
from ..models.rapid_chat_sink_in_mode import RapidChatSinkInMode
from ..types import UNSET, Unset

T = TypeVar("T", bound="RapidChatSinkIn")


@_attrs_define
class RapidChatSinkIn:
    """
    Attributes:
        slug (str):
        kind (RapidChatSinkInKind | Unset):  Default: RapidChatSinkInKind.WIKI.
        mode (RapidChatSinkInMode | Unset):  Default: RapidChatSinkInMode.REPLACE.
    """

    slug: str
    kind: RapidChatSinkInKind | Unset = RapidChatSinkInKind.WIKI
    mode: RapidChatSinkInMode | Unset = RapidChatSinkInMode.REPLACE

    def to_dict(self) -> dict[str, Any]:
        slug = self.slug

        kind: str | Unset = UNSET
        if not isinstance(self.kind, Unset):
            kind = self.kind.value

        mode: str | Unset = UNSET
        if not isinstance(self.mode, Unset):
            mode = self.mode.value

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "slug": slug,
            }
        )
        if kind is not UNSET:
            field_dict["kind"] = kind
        if mode is not UNSET:
            field_dict["mode"] = mode

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        slug = d.pop("slug")

        _kind = d.pop("kind", UNSET)
        kind: RapidChatSinkInKind | Unset
        if isinstance(_kind, Unset):
            kind = UNSET
        else:
            kind = RapidChatSinkInKind(_kind)

        _mode = d.pop("mode", UNSET)
        mode: RapidChatSinkInMode | Unset
        if isinstance(_mode, Unset):
            mode = UNSET
        else:
            mode = RapidChatSinkInMode(_mode)

        rapid_chat_sink_in = cls(
            slug=slug,
            kind=kind,
            mode=mode,
        )

        return rapid_chat_sink_in
