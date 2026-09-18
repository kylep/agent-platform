from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="RelayFace")


@_attrs_define
class RelayFace:
    """An agent's avatar: its own `AgentDef.icon` when set, else the
    deterministic fallback so `news` looks the same in every client forever.
    `image_url` is the agent's picture (docs/design/23) — the thumb route of
    its `image_artifact_id` — which a client shows over the emoji when set.
    Optional so every producer of a face keeps working; `faces_for` fills it.

        Attributes:
            emoji (str):
            hue (int):
            image_url (None | str | Unset):
    """

    emoji: str
    hue: int
    image_url: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        emoji = self.emoji

        hue = self.hue

        image_url: None | str | Unset
        if isinstance(self.image_url, Unset):
            image_url = UNSET
        else:
            image_url = self.image_url

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "emoji": emoji,
                "hue": hue,
            }
        )
        if image_url is not UNSET:
            field_dict["image_url"] = image_url

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        emoji = d.pop("emoji")

        hue = d.pop("hue")

        def _parse_image_url(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        image_url = _parse_image_url(d.pop("image_url", UNSET))

        relay_face = cls(
            emoji=emoji,
            hue=hue,
            image_url=image_url,
        )

        relay_face.additional_properties = d
        return relay_face

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
