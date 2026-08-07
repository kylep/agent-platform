from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.agent_tools_labels import AgentToolsLabels


T = TypeVar("T", bound="AgentTools")


@_attrs_define
class AgentTools:
    """
    Attributes:
        tools (list[str]):
        labels (AgentToolsLabels | Unset):
    """

    tools: list[str]
    labels: AgentToolsLabels | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        tools = self.tools

        labels: dict[str, Any] | Unset = UNSET
        if not isinstance(self.labels, Unset):
            labels = self.labels.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "tools": tools,
            }
        )
        if labels is not UNSET:
            field_dict["labels"] = labels

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.agent_tools_labels import AgentToolsLabels

        d = dict(src_dict)
        tools = cast(list[str], d.pop("tools"))

        _labels = d.pop("labels", UNSET)
        labels: AgentToolsLabels | Unset
        if isinstance(_labels, Unset):
            labels = UNSET
        else:
            labels = AgentToolsLabels.from_dict(_labels)

        agent_tools = cls(
            tools=tools,
            labels=labels,
        )

        agent_tools.additional_properties = d
        return agent_tools

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
