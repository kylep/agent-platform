from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="WhoAmI")


@_attrs_define
class WhoAmI:
    """Verified caller identity for the MCP broker's grant checks.

    Attributes:
        agent (None | str):
        principal (str):
        role (str):
        run_id (None | str):
        tools (list[str] | None):
    """

    agent: None | str
    principal: str
    role: str
    run_id: None | str
    tools: list[str] | None
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        agent: None | str
        agent = self.agent

        principal = self.principal

        role = self.role

        run_id: None | str
        run_id = self.run_id

        tools: list[str] | None
        if isinstance(self.tools, list):
            tools = self.tools

        else:
            tools = self.tools

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "agent": agent,
                "principal": principal,
                "role": role,
                "run_id": run_id,
                "tools": tools,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)

        def _parse_agent(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        agent = _parse_agent(d.pop("agent"))

        principal = d.pop("principal")

        role = d.pop("role")

        def _parse_run_id(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        run_id = _parse_run_id(d.pop("run_id"))

        def _parse_tools(data: object) -> list[str] | None:
            if data is None:
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                tools_type_0 = cast(list[str], data)

                return tools_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[str] | None, data)

        tools = _parse_tools(d.pop("tools"))

        who_am_i = cls(
            agent=agent,
            principal=principal,
            role=role,
            run_id=run_id,
            tools=tools,
        )

        who_am_i.additional_properties = d
        return who_am_i

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
