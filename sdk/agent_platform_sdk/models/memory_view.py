from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="MemoryView")


@_attrs_define
class MemoryView:
    """
    Attributes:
        agent (str):
        content (str):
        created_at (None | str):
        id (str):
        key (None | str):
        tags (list[str]):
        updated_at (None | str):
        project_id (None | str | Unset):
        team_id (None | str | Unset):
    """

    agent: str
    content: str
    created_at: None | str
    id: str
    key: None | str
    tags: list[str]
    updated_at: None | str
    project_id: None | str | Unset = UNSET
    team_id: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        agent = self.agent

        content = self.content

        created_at: None | str
        created_at = self.created_at

        id = self.id

        key: None | str
        key = self.key

        tags = self.tags

        updated_at: None | str
        updated_at = self.updated_at

        project_id: None | str | Unset
        if isinstance(self.project_id, Unset):
            project_id = UNSET
        else:
            project_id = self.project_id

        team_id: None | str | Unset
        if isinstance(self.team_id, Unset):
            team_id = UNSET
        else:
            team_id = self.team_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "agent": agent,
                "content": content,
                "created_at": created_at,
                "id": id,
                "key": key,
                "tags": tags,
                "updated_at": updated_at,
            }
        )
        if project_id is not UNSET:
            field_dict["project_id"] = project_id
        if team_id is not UNSET:
            field_dict["team_id"] = team_id

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        agent = d.pop("agent")

        content = d.pop("content")

        def _parse_created_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        created_at = _parse_created_at(d.pop("created_at"))

        id = d.pop("id")

        def _parse_key(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        key = _parse_key(d.pop("key"))

        tags = cast(list[str], d.pop("tags"))

        def _parse_updated_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        updated_at = _parse_updated_at(d.pop("updated_at"))

        def _parse_project_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        project_id = _parse_project_id(d.pop("project_id", UNSET))

        def _parse_team_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        team_id = _parse_team_id(d.pop("team_id", UNSET))

        memory_view = cls(
            agent=agent,
            content=content,
            created_at=created_at,
            id=id,
            key=key,
            tags=tags,
            updated_at=updated_at,
            project_id=project_id,
            team_id=team_id,
        )

        memory_view.additional_properties = d
        return memory_view

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
