from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="ConversationScopeIn")


@_attrs_define
class ConversationScopeIn:
    """
    Attributes:
        project_slug (None | str | Unset):
        team_slug (None | str | Unset):
    """

    project_slug: None | str | Unset = UNSET
    team_slug: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        project_slug: None | str | Unset
        if isinstance(self.project_slug, Unset):
            project_slug = UNSET
        else:
            project_slug = self.project_slug

        team_slug: None | str | Unset
        if isinstance(self.team_slug, Unset):
            team_slug = UNSET
        else:
            team_slug = self.team_slug

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if project_slug is not UNSET:
            field_dict["project_slug"] = project_slug
        if team_slug is not UNSET:
            field_dict["team_slug"] = team_slug

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)

        def _parse_project_slug(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        project_slug = _parse_project_slug(d.pop("project_slug", UNSET))

        def _parse_team_slug(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        team_slug = _parse_team_slug(d.pop("team_slug", UNSET))

        conversation_scope_in = cls(
            project_slug=project_slug,
            team_slug=team_slug,
        )

        conversation_scope_in.additional_properties = d
        return conversation_scope_in

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
