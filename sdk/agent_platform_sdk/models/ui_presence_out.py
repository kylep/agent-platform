from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.ui_presence_session import UiPresenceSession


T = TypeVar("T", bound="UiPresenceOut")


@_attrs_define
class UiPresenceOut:
    """
    Attributes:
        document_id (str):
        server_now (str):
        sessions (list[UiPresenceSession]):
    """

    document_id: str
    server_now: str
    sessions: list[UiPresenceSession]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        document_id = self.document_id

        server_now = self.server_now

        sessions = []
        for sessions_item_data in self.sessions:
            sessions_item = sessions_item_data.to_dict()
            sessions.append(sessions_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "document_id": document_id,
                "server_now": server_now,
                "sessions": sessions,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.ui_presence_session import UiPresenceSession

        d = dict(src_dict)
        document_id = d.pop("document_id")

        server_now = d.pop("server_now")

        sessions = []
        _sessions = d.pop("sessions")
        for sessions_item_data in _sessions:
            sessions_item = UiPresenceSession.from_dict(sessions_item_data)

            sessions.append(sessions_item)

        ui_presence_out = cls(
            document_id=document_id,
            server_now=server_now,
            sessions=sessions,
        )

        ui_presence_out.additional_properties = d
        return ui_presence_out

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
