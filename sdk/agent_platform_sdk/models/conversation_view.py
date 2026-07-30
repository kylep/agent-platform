from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="ConversationView")


@_attrs_define
class ConversationView:
    """
    Attributes:
        agent (str):
        connector (str):
        created_at (None | str):
        external_ref (None | str):
        id (str):
        status (str):
        title (str):
        updated_at (None | str):
    """

    agent: str
    connector: str
    created_at: None | str
    external_ref: None | str
    id: str
    status: str
    title: str
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
                "updated_at": updated_at,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
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

        def _parse_updated_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        updated_at = _parse_updated_at(d.pop("updated_at"))

        conversation_view = cls(
            agent=agent,
            connector=connector,
            created_at=created_at,
            external_ref=external_ref,
            id=id,
            status=status,
            title=title,
            updated_at=updated_at,
        )

        conversation_view.additional_properties = d
        return conversation_view

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
