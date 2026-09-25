from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="TeamIn")


@_attrs_define
class TeamIn:
    """
    Attributes:
        name (str):
        slug (str):
        agents (list[str] | Unset):
        description (str | Unset):  Default: ''.
        humans (list[str] | Unset):
    """

    name: str
    slug: str
    agents: list[str] | Unset = UNSET
    description: str | Unset = ""
    humans: list[str] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        slug = self.slug

        agents: list[str] | Unset = UNSET
        if not isinstance(self.agents, Unset):
            agents = self.agents

        description = self.description

        humans: list[str] | Unset = UNSET
        if not isinstance(self.humans, Unset):
            humans = self.humans

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
        if humans is not UNSET:
            field_dict["humans"] = humans

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        name = d.pop("name")

        slug = d.pop("slug")

        agents = cast(list[str], d.pop("agents", UNSET))

        description = d.pop("description", UNSET)

        humans = cast(list[str], d.pop("humans", UNSET))

        team_in = cls(
            name=name,
            slug=slug,
            agents=agents,
            description=description,
            humans=humans,
        )

        team_in.additional_properties = d
        return team_in

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
