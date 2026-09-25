from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from typing_extensions import Self

from ..models.relay_channel_patch_reply_mode_type_0 import (
    RelayChannelPatchReplyModeType0,
)
from ..types import UNSET, Unset

T = TypeVar("T", bound="RelayChannelPatch")


@_attrs_define
class RelayChannelPatch:
    """
    Attributes:
        archived (bool | None | Unset):
        name (None | str | Unset):
        reply_mode (None | RelayChannelPatchReplyModeType0 | Unset):
        ticket_prefix (None | str | Unset):
        topic (None | str | Unset):
    """

    archived: bool | None | Unset = UNSET
    name: None | str | Unset = UNSET
    reply_mode: None | RelayChannelPatchReplyModeType0 | Unset = UNSET
    ticket_prefix: None | str | Unset = UNSET
    topic: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        archived: bool | None | Unset
        if isinstance(self.archived, Unset):
            archived = UNSET
        else:
            archived = self.archived

        name: None | str | Unset
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

        reply_mode: None | str | Unset
        if isinstance(self.reply_mode, Unset):
            reply_mode = UNSET
        elif isinstance(self.reply_mode, RelayChannelPatchReplyModeType0):
            reply_mode = self.reply_mode.value
        else:
            reply_mode = self.reply_mode

        ticket_prefix: None | str | Unset
        if isinstance(self.ticket_prefix, Unset):
            ticket_prefix = UNSET
        else:
            ticket_prefix = self.ticket_prefix

        topic: None | str | Unset
        if isinstance(self.topic, Unset):
            topic = UNSET
        else:
            topic = self.topic

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if archived is not UNSET:
            field_dict["archived"] = archived
        if name is not UNSET:
            field_dict["name"] = name
        if reply_mode is not UNSET:
            field_dict["reply_mode"] = reply_mode
        if ticket_prefix is not UNSET:
            field_dict["ticket_prefix"] = ticket_prefix
        if topic is not UNSET:
            field_dict["topic"] = topic

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)

        def _parse_archived(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        archived = _parse_archived(d.pop("archived", UNSET))

        def _parse_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        name = _parse_name(d.pop("name", UNSET))

        def _parse_reply_mode(
            data: object,
        ) -> None | RelayChannelPatchReplyModeType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                reply_mode_type_0 = RelayChannelPatchReplyModeType0(data)

                return reply_mode_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | RelayChannelPatchReplyModeType0 | Unset, data)

        reply_mode = _parse_reply_mode(d.pop("reply_mode", UNSET))

        def _parse_ticket_prefix(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        ticket_prefix = _parse_ticket_prefix(d.pop("ticket_prefix", UNSET))

        def _parse_topic(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        topic = _parse_topic(d.pop("topic", UNSET))

        relay_channel_patch = cls(
            archived=archived,
            name=name,
            reply_mode=reply_mode,
            ticket_prefix=ticket_prefix,
            topic=topic,
        )

        return relay_channel_patch
