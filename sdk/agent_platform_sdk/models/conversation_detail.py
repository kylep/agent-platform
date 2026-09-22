from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.conversation_turn import ConversationTurn


T = TypeVar("T", bound="ConversationDetail")


@_attrs_define
class ConversationDetail:
    """
    Attributes:
        agent (str):
        connector (str):
        created_at (None | str):
        external_ref (None | str):
        id (str):
        status (str):
        title (str):
        turns (list[ConversationTurn]):
        updated_at (None | str):
    """

    agent: str
    connector: str
    created_at: None | str
    external_ref: None | str
    id: str
    status: str
    title: str
    turns: list[ConversationTurn]
    updated_at: None | str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        agent = self.agent

        connector = self.connector

        created_at: None | str
        created_at = self.created_at

        external_ref: None | str
        external_ref = self.external_ref

        id = self.id

        status = self.status

        title = self.title

        turns = []
        for turns_item_data in self.turns:
            turns_item = turns_item_data.to_dict()
            turns.append(turns_item)

        updated_at: None | str
        updated_at = self.updated_at

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "agent": agent,
                "connector": connector,
                "created_at": created_at,
                "external_ref": external_ref,
                "id": id,
                "status": status,
                "title": title,
                "turns": turns,
                "updated_at": updated_at,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.conversation_turn import ConversationTurn

        d = dict(src_dict)
        agent = d.pop("agent")

        connector = d.pop("connector")

        def _parse_created_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        created_at = _parse_created_at(d.pop("created_at"))

        def _parse_external_ref(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        external_ref = _parse_external_ref(d.pop("external_ref"))

        id = d.pop("id")

        status = d.pop("status")

        title = d.pop("title")

        turns = []
        _turns = d.pop("turns")
        for turns_item_data in _turns:
            turns_item = ConversationTurn.from_dict(turns_item_data)

            turns.append(turns_item)

        def _parse_updated_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        updated_at = _parse_updated_at(d.pop("updated_at"))

        conversation_detail = cls(
            agent=agent,
            connector=connector,
            created_at=created_at,
            external_ref=external_ref,
            id=id,
            status=status,
            title=title,
            turns=turns,
            updated_at=updated_at,
        )

        conversation_detail.additional_properties = d
        return conversation_detail

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
