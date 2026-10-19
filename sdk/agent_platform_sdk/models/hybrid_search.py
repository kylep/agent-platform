from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="HybridSearch")


@_attrs_define
class HybridSearch:
    """
    Attributes:
        q (str):
        current_project (bool | Unset):  Default: False.
        current_team (bool | Unset):  Default: False.
        limit (int | Unset):  Default: 20.
    """

    q: str
    current_project: bool | Unset = False
    current_team: bool | Unset = False
    limit: int | Unset = 20
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        q = self.q

        current_project = self.current_project

        current_team = self.current_team

        limit = self.limit

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "q": q,
            }
        )
        if current_project is not UNSET:
            field_dict["current_project"] = current_project
        if current_team is not UNSET:
            field_dict["current_team"] = current_team
        if limit is not UNSET:
            field_dict["limit"] = limit

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        q = d.pop("q")

        current_project = d.pop("current_project", UNSET)

        current_team = d.pop("current_team", UNSET)

        limit = d.pop("limit", UNSET)

        hybrid_search = cls(
            q=q,
            current_project=current_project,
            current_team=current_team,
            limit=limit,
        )

        hybrid_search.additional_properties = d
        return hybrid_search

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
