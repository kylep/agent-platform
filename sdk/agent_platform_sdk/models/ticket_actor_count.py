from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.relay_face import RelayFace


T = TypeVar("T", bound="TicketActorCount")


@_attrs_define
class TicketActorCount:
    """One row of the board's "Today" strip: who moved how much.

    Attributes:
        actor (str):
        count (int):
        label (str):
        face (None | RelayFace | Unset):
    """

    actor: str
    count: int
    label: str
    face: None | RelayFace | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.relay_face import RelayFace

        actor = self.actor

        count = self.count

        label = self.label

        face: dict[str, Any] | None | Unset
        if isinstance(self.face, Unset):
            face = UNSET
        elif isinstance(self.face, RelayFace):
            face = self.face.to_dict()
        else:
            face = self.face

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "actor": actor,
                "count": count,
                "label": label,
            }
        )
        if face is not UNSET:
            field_dict["face"] = face

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.relay_face import RelayFace

        d = dict(src_dict)
        actor = d.pop("actor")

        count = d.pop("count")

        label = d.pop("label")

        def _parse_face(data: object) -> None | RelayFace | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                face_type_0 = RelayFace.from_dict(data)

                return face_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | RelayFace | Unset, data)

        face = _parse_face(d.pop("face", UNSET))

        ticket_actor_count = cls(
            actor=actor,
            count=count,
            label=label,
            face=face,
        )

        ticket_actor_count.additional_properties = d
        return ticket_actor_count

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
