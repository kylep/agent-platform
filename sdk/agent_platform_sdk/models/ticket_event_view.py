from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.relay_face import RelayFace


T = TypeVar("T", bound="TicketEventView")


@_attrs_define
class TicketEventView:
    """
    Attributes:
        actor (str):
        created_at (None | str):
        from_value (None | str):
        id (str):
        kind (str):
        message_id (None | str):
        reason (None | str):
        run_id (None | str):
        ticket_id (str):
        to_value (None | str):
        actor_face (None | RelayFace | Unset):
    """

    actor: str
    created_at: None | str
    from_value: None | str
    id: str
    kind: str
    message_id: None | str
    reason: None | str
    run_id: None | str
    ticket_id: str
    to_value: None | str
    actor_face: None | RelayFace | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.relay_face import RelayFace

        actor = self.actor

        created_at: None | str
        created_at = self.created_at

        from_value: None | str
        from_value = self.from_value

        id = self.id

        kind = self.kind

        message_id: None | str
        message_id = self.message_id

        reason: None | str
        reason = self.reason

        run_id: None | str
        run_id = self.run_id

        ticket_id = self.ticket_id

        to_value: None | str
        to_value = self.to_value

        actor_face: dict[str, Any] | None | Unset
        if isinstance(self.actor_face, Unset):
            actor_face = UNSET
        elif isinstance(self.actor_face, RelayFace):
            actor_face = self.actor_face.to_dict()
        else:
            actor_face = self.actor_face

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "actor": actor,
                "created_at": created_at,
                "from_value": from_value,
                "id": id,
                "kind": kind,
                "message_id": message_id,
                "reason": reason,
                "run_id": run_id,
                "ticket_id": ticket_id,
                "to_value": to_value,
            }
        )
        if actor_face is not UNSET:
            field_dict["actor_face"] = actor_face

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.relay_face import RelayFace

        d = dict(src_dict)
        actor = d.pop("actor")

        def _parse_created_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        created_at = _parse_created_at(d.pop("created_at"))

        def _parse_from_value(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        from_value = _parse_from_value(d.pop("from_value"))

        id = d.pop("id")

        kind = d.pop("kind")

        def _parse_message_id(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        message_id = _parse_message_id(d.pop("message_id"))

        def _parse_reason(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        reason = _parse_reason(d.pop("reason"))

        def _parse_run_id(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        run_id = _parse_run_id(d.pop("run_id"))

        ticket_id = d.pop("ticket_id")

        def _parse_to_value(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        to_value = _parse_to_value(d.pop("to_value"))

        def _parse_actor_face(data: object) -> None | RelayFace | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                actor_face_type_0 = RelayFace.from_dict(data)

                return actor_face_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | RelayFace | Unset, data)

        actor_face = _parse_actor_face(d.pop("actor_face", UNSET))

        ticket_event_view = cls(
            actor=actor,
            created_at=created_at,
            from_value=from_value,
            id=id,
            kind=kind,
            message_id=message_id,
            reason=reason,
            run_id=run_id,
            ticket_id=ticket_id,
            to_value=to_value,
            actor_face=actor_face,
        )

        ticket_event_view.additional_properties = d
        return ticket_event_view

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
