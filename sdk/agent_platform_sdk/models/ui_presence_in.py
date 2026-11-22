from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.ui_presence_in_state import UiPresenceInState
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.ui_presence_cursor import UiPresenceCursor


T = TypeVar("T", bound="UiPresenceIn")


@_attrs_define
class UiPresenceIn:
    """
    Attributes:
        document_id (str):
        page_id (str):
        state (UiPresenceInState):
        version (int):
        cursor (None | UiPresenceCursor | Unset):
        node_id (None | str | Unset):
    """

    document_id: str
    page_id: str
    state: UiPresenceInState
    version: int
    cursor: None | UiPresenceCursor | Unset = UNSET
    node_id: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.ui_presence_cursor import UiPresenceCursor

        document_id = self.document_id

        page_id = self.page_id

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

        field_dict.update(
            {
                "document_id": document_id,
                "page_id": page_id,
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
        document_id = d.pop("document_id")

        page_id = d.pop("page_id")

        state = UiPresenceInState(d.pop("state"))

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

        ui_presence_in = cls(
            document_id=document_id,
            page_id=page_id,
            state=state,
            version=version,
            cursor=cursor,
            node_id=node_id,
        )

        return ui_presence_in
