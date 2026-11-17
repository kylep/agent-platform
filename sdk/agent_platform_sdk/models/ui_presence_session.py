from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.ui_presence_session_state import UiPresenceSessionState
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.ui_presence_cursor import UiPresenceCursor


T = TypeVar("T", bound="UiPresenceSession")


@_attrs_define
class UiPresenceSession:
    """
    Attributes:
        client_session (str):
        expires_at (str):
        label (str):
        page_id (str):
        participant (str):
        sequence (int):
        state (UiPresenceSessionState):
        version (int):
        cursor (None | UiPresenceCursor | Unset):
        node_id (None | str | Unset):
    """

    client_session: str
    expires_at: str
    label: str
    page_id: str
    participant: str
    sequence: int
    state: UiPresenceSessionState
    version: int
    cursor: None | UiPresenceCursor | Unset = UNSET
    node_id: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.ui_presence_cursor import UiPresenceCursor

        client_session = self.client_session

        expires_at = self.expires_at

        label = self.label

        page_id = self.page_id

        participant = self.participant

        sequence = self.sequence

        state = self.state.value

        version = self.version

        cursor: dict[str, Any] | None | Unset
        if isinstance(self.cursor, Unset):
            cursor = UNSET
        elif isinstance(self.cursor, UiPresenceCursor):
            cursor = self.cursor.to_dict()
        else:
            cursor = self.cursor

        node_id: None | str | Unset
        if isinstance(self.node_id, Unset):
            node_id = UNSET
        else:
            node_id = self.node_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "client_session": client_session,
                "expires_at": expires_at,
                "label": label,
                "page_id": page_id,
                "participant": participant,
                "sequence": sequence,
                "state": state,
                "version": version,
            }
        )
        if cursor is not UNSET:
            field_dict["cursor"] = cursor
        if node_id is not UNSET:
            field_dict["node_id"] = node_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.ui_presence_cursor import UiPresenceCursor

        d = dict(src_dict)
        client_session = d.pop("client_session")

        expires_at = d.pop("expires_at")

        label = d.pop("label")

        page_id = d.pop("page_id")

        participant = d.pop("participant")

        sequence = d.pop("sequence")

        state = UiPresenceSessionState(d.pop("state"))

        version = d.pop("version")

        def _parse_cursor(data: object) -> None | UiPresenceCursor | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                cursor_type_0 = UiPresenceCursor.from_dict(data)

                return cursor_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | UiPresenceCursor | Unset, data)

        cursor = _parse_cursor(d.pop("cursor", UNSET))

        def _parse_node_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        node_id = _parse_node_id(d.pop("node_id", UNSET))

        ui_presence_session = cls(
            client_session=client_session,
            expires_at=expires_at,
            label=label,
            page_id=page_id,
            participant=participant,
            sequence=sequence,
            state=state,
            version=version,
            cursor=cursor,
            node_id=node_id,
        )

        ui_presence_session.additional_properties = d
        return ui_presence_session

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
