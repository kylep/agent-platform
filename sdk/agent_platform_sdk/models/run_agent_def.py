from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="RunAgentDef")


@_attrs_define
class RunAgentDef:
    """An agent definition as the RUN POD needs it (docs/design/15).

    Not the full row: only what the pod materializes into
    `~/.claude/agents/<name>.md` and derives its permission flags from. The two
    grant lists are EXPLICIT — an empty list means no tools, never "everything"
    — because the runner turns them straight into the file's `tools:` line.

        Attributes:
            name (str):
            prompt (str):
            harness_tools (list[str] | Unset):
            model (str | Unset):  Default: ''.
            platform_tools (list[str] | Unset):
            skills (list[str] | Unset):
    """

    name: str
    prompt: str
    harness_tools: list[str] | Unset = UNSET
    model: str | Unset = ""
    platform_tools: list[str] | Unset = UNSET
    skills: list[str] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        prompt = self.prompt

        harness_tools: list[str] | Unset = UNSET
        if not isinstance(self.harness_tools, Unset):
            harness_tools = self.harness_tools

        model = self.model

        platform_tools: list[str] | Unset = UNSET
        if not isinstance(self.platform_tools, Unset):
            platform_tools = self.platform_tools

        skills: list[str] | Unset = UNSET
        if not isinstance(self.skills, Unset):
            skills = self.skills

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "name": name,
                "prompt": prompt,
            }
        )
        if harness_tools is not UNSET:
            field_dict["harness_tools"] = harness_tools
        if model is not UNSET:
            field_dict["model"] = model
        if platform_tools is not UNSET:
            field_dict["platform_tools"] = platform_tools
        if skills is not UNSET:
            field_dict["skills"] = skills

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        name = d.pop("name")

        prompt = d.pop("prompt")

        harness_tools = cast(list[str], d.pop("harness_tools", UNSET))

        model = d.pop("model", UNSET)

        platform_tools = cast(list[str], d.pop("platform_tools", UNSET))

        skills = cast(list[str], d.pop("skills", UNSET))

        run_agent_def = cls(
            name=name,
            prompt=prompt,
            harness_tools=harness_tools,
            model=model,
            platform_tools=platform_tools,
            skills=skills,
        )

        run_agent_def.additional_properties = d
        return run_agent_def

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
