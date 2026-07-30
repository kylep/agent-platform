from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="CreateAgentIn")


@_attrs_define
class CreateAgentIn:
    """
    Attributes:
        name (str):
        concurrency (int | Unset):  Default: 1.
        description (str | Unset):  Default: ''.
        model (str | Unset):  Default: ''.
        prompt (str | Unset):  Default: ''.
        role (str | Unset):  Default: 'operator'.
        skills (list[str] | Unset):
        timeout_seconds (int | Unset):  Default: 1800.
        tools (list[str] | Unset):
    """

    name: str
    concurrency: int | Unset = 1
    description: str | Unset = ""
    model: str | Unset = ""
    prompt: str | Unset = ""
    role: str | Unset = "operator"
    skills: list[str] | Unset = UNSET
    timeout_seconds: int | Unset = 1800
    tools: list[str] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        concurrency = self.concurrency

        description = self.description

        model = self.model

        prompt = self.prompt

        role = self.role

        skills: list[str] | Unset = UNSET
        if not isinstance(self.skills, Unset):
            skills = self.skills

        timeout_seconds = self.timeout_seconds

        tools: list[str] | Unset = UNSET
        if not isinstance(self.tools, Unset):
            tools = self.tools

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "name": name,
            }
        )
        if concurrency is not UNSET:
            field_dict["concurrency"] = concurrency
        if description is not UNSET:
            field_dict["description"] = description
        if model is not UNSET:
            field_dict["model"] = model
        if prompt is not UNSET:
            field_dict["prompt"] = prompt
        if role is not UNSET:
            field_dict["role"] = role
        if skills is not UNSET:
            field_dict["skills"] = skills
        if timeout_seconds is not UNSET:
            field_dict["timeout_seconds"] = timeout_seconds
        if tools is not UNSET:
            field_dict["tools"] = tools

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        name = d.pop("name")

        concurrency = d.pop("concurrency", UNSET)

        description = d.pop("description", UNSET)

        model = d.pop("model", UNSET)

        prompt = d.pop("prompt", UNSET)

        role = d.pop("role", UNSET)

        skills = cast(list[str], d.pop("skills", UNSET))

        timeout_seconds = d.pop("timeout_seconds", UNSET)

        tools = cast(list[str], d.pop("tools", UNSET))

        create_agent_in = cls(
            name=name,
            concurrency=concurrency,
            description=description,
            model=model,
            prompt=prompt,
            role=role,
            skills=skills,
            timeout_seconds=timeout_seconds,
            tools=tools,
        )

        create_agent_in.additional_properties = d
        return create_agent_in

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
