from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="ProjectIn")


@_attrs_define
class ProjectIn:
    """
    Attributes:
        name (str):
        slug (str):
        agents (list[str] | Unset):
        description (str | Unset):  Default: ''.
        team_slug (None | str | Unset):
    """

    name: str
    slug: str
    agents: list[str] | Unset = UNSET
    description: str | Unset = ""
    team_slug: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        slug = self.slug

        agents: list[str] | Unset = UNSET
        if not isinstance(self.agents, Unset):
            agents = self.agents

        description = self.description

        team_slug: None | str | Unset
        if isinstance(self.team_slug, Unset):
            team_slug = UNSET
        else:
            team_slug = self.team_slug

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "name": name,
                "slug": slug,
            }
        )
        if agents is not UNSET:
            field_dict["agents"] = agents
        if description is not UNSET:
            field_dict["description"] = description
        if team_slug is not UNSET:
            field_dict["team_slug"] = team_slug

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        name = d.pop("name")

        slug = d.pop("slug")

        agents = cast(list[str], d.pop("agents", UNSET))

        description = d.pop("description", UNSET)

        def _parse_team_slug(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        team_slug = _parse_team_slug(d.pop("team_slug", UNSET))

        project_in = cls(
            name=name,
            slug=slug,
            agents=agents,
            description=description,
            team_slug=team_slug,
        )

        project_in.additional_properties = d
        return project_in

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
