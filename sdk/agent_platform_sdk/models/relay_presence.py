from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.relay_face import RelayFace


T = TypeVar("T", bound="RelayPresence")


@_attrs_define
class RelayPresence:
    """
    Attributes:
        agent (str):
        face (RelayFace): An agent's avatar: its own `AgentDef.icon` when set, else the
            deterministic fallback so `news` looks the same in every client forever.
            `image_url` is the agent's picture (docs/design/23) — the thumb route of
            its `image_artifact_id` — which a client shows over the emoji when set.
            Optional so every producer of a face keeps working; `faces_for` fills it.
        state (str):
        thinking_in (list[str]):
    """

    agent: str
    face: RelayFace
    state: str
    thinking_in: list[str]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        agent = self.agent

        face = self.face.to_dict()

        state = self.state

        thinking_in = self.thinking_in

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "agent": agent,
                "face": face,
                "state": state,
                "thinking_in": thinking_in,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.relay_face import RelayFace

        d = dict(src_dict)
        agent = d.pop("agent")

        face = RelayFace.from_dict(d.pop("face"))

        state = d.pop("state")

        thinking_in = cast(list[str], d.pop("thinking_in"))

        relay_presence = cls(
            agent=agent,
            face=face,
            state=state,
            thinking_in=thinking_in,
        )

        relay_presence.additional_properties = d
        return relay_presence

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
