from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from typing_extensions import Self

T = TypeVar("T", bound="RelayNotifyIn")


@_attrs_define
class RelayNotifyIn:
    """A system row from something that is not a participant (docs/design/25):
    an app key announcing what it recorded. `channel` is a name, `#name` or an
    id; the text is flattened to one line and mentions in it summon nobody.

        Attributes:
            channel (str):
            text (str):
    """

    channel: str
    text: str

    def to_dict(self) -> dict[str, Any]:
        channel = self.channel

        text = self.text

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "channel": channel,
                "text": text,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        channel = d.pop("channel")

        text = d.pop("text")

        relay_notify_in = cls(
            channel=channel,
            text=text,
        )

        return relay_notify_in
