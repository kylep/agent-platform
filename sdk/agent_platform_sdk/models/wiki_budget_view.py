from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.wiki_agent_budget import WikiAgentBudget


T = TypeVar("T", bound="WikiBudgetView")


@_attrs_define
class WikiBudgetView:
    """
    Attributes:
        limit (int):
        agents (list[WikiAgentBudget] | Unset):
    """

    limit: int
    agents: list[WikiAgentBudget] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        limit = self.limit

        agents: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.agents, Unset):
            agents = []
            for agents_item_data in self.agents:
                agents_item = agents_item_data.to_dict()
                agents.append(agents_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "limit": limit,
            }
        )
        if agents is not UNSET:
            field_dict["agents"] = agents

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.wiki_agent_budget import WikiAgentBudget

        d = dict(src_dict)
        limit = d.pop("limit")

        _agents = d.pop("agents", UNSET)
        agents: list[WikiAgentBudget] | Unset = UNSET
        if _agents is not UNSET:
            agents = []
            for agents_item_data in _agents:
                agents_item = WikiAgentBudget.from_dict(agents_item_data)

                agents.append(agents_item)

        wiki_budget_view = cls(
            limit=limit,
            agents=agents,
        )

        wiki_budget_view.additional_properties = d
        return wiki_budget_view

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
