from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.agent_def_out_entrypoints import AgentDefOutEntrypoints


T = TypeVar("T", bound="AgentDefOut")


@_attrs_define
class AgentDefOut:
    """An agent definition as the API returns it.

    Attributes:
        name (str):
        can_invoke (bool | Unset):  Default: False.
        concurrency (int | Unset):  Default: 1.
        description (str | Unset):  Default: ''.
        enabled (bool | Unset):  Default: True.
        entrypoints (AgentDefOutEntrypoints | Unset):
        harness_tools (list[str] | Unset):
        model (str | Unset):  Default: ''.
        platform_tools (list[str] | Unset):
        prompt (str | Unset):  Default: ''.
        result_topic (str | Unset):  Default: ''.
        role (str | Unset):  Default: 'operator'.
        secrets (list[str] | Unset):
        skills (list[str] | Unset):
        system (bool | Unset):  Default: False.
        timeout_seconds (int | Unset):  Default: 1800.
        transcript_retention_days (int | None | Unset):
    """

    name: str
    can_invoke: bool | Unset = False
    concurrency: int | Unset = 1
    description: str | Unset = ""
    enabled: bool | Unset = True
    entrypoints: AgentDefOutEntrypoints | Unset = UNSET
    harness_tools: list[str] | Unset = UNSET
    model: str | Unset = ""
    platform_tools: list[str] | Unset = UNSET
    prompt: str | Unset = ""
    result_topic: str | Unset = ""
    role: str | Unset = "operator"
    secrets: list[str] | Unset = UNSET
    skills: list[str] | Unset = UNSET
    system: bool | Unset = False
    timeout_seconds: int | Unset = 1800
    transcript_retention_days: int | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        can_invoke = self.can_invoke

        concurrency = self.concurrency

        description = self.description

        enabled = self.enabled

        entrypoints: dict[str, Any] | Unset = UNSET
        if not isinstance(self.entrypoints, Unset):
            entrypoints = self.entrypoints.to_dict()

        harness_tools: list[str] | Unset = UNSET
        if not isinstance(self.harness_tools, Unset):
            harness_tools = self.harness_tools

        model = self.model

        platform_tools: list[str] | Unset = UNSET
        if not isinstance(self.platform_tools, Unset):
            platform_tools = self.platform_tools

        prompt = self.prompt

        result_topic = self.result_topic

        role = self.role

        secrets: list[str] | Unset = UNSET
        if not isinstance(self.secrets, Unset):
            secrets = self.secrets

        skills: list[str] | Unset = UNSET
        if not isinstance(self.skills, Unset):
            skills = self.skills

        system = self.system

        timeout_seconds = self.timeout_seconds

        transcript_retention_days: int | None | Unset
        if isinstance(self.transcript_retention_days, Unset):
            transcript_retention_days = UNSET
        else:
            transcript_retention_days = self.transcript_retention_days

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "name": name,
            }
        )
        if can_invoke is not UNSET:
            field_dict["can_invoke"] = can_invoke
        if concurrency is not UNSET:
            field_dict["concurrency"] = concurrency
        if description is not UNSET:
            field_dict["description"] = description
        if enabled is not UNSET:
            field_dict["enabled"] = enabled
        if entrypoints is not UNSET:
            field_dict["entrypoints"] = entrypoints
        if harness_tools is not UNSET:
            field_dict["harness_tools"] = harness_tools
        if model is not UNSET:
            field_dict["model"] = model
        if platform_tools is not UNSET:
            field_dict["platform_tools"] = platform_tools
        if prompt is not UNSET:
            field_dict["prompt"] = prompt
        if result_topic is not UNSET:
            field_dict["result_topic"] = result_topic
        if role is not UNSET:
            field_dict["role"] = role
        if secrets is not UNSET:
            field_dict["secrets"] = secrets
        if skills is not UNSET:
            field_dict["skills"] = skills
        if system is not UNSET:
            field_dict["system"] = system
        if timeout_seconds is not UNSET:
            field_dict["timeout_seconds"] = timeout_seconds
        if transcript_retention_days is not UNSET:
            field_dict["transcript_retention_days"] = transcript_retention_days

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.agent_def_out_entrypoints import AgentDefOutEntrypoints

        d = dict(src_dict)
        name = d.pop("name")

        can_invoke = d.pop("can_invoke", UNSET)

        concurrency = d.pop("concurrency", UNSET)

        description = d.pop("description", UNSET)

        enabled = d.pop("enabled", UNSET)

        _entrypoints = d.pop("entrypoints", UNSET)
        entrypoints: AgentDefOutEntrypoints | Unset
        if isinstance(_entrypoints, Unset):
            entrypoints = UNSET
        else:
            entrypoints = AgentDefOutEntrypoints.from_dict(_entrypoints)

        harness_tools = cast(list[str], d.pop("harness_tools", UNSET))

        model = d.pop("model", UNSET)

        platform_tools = cast(list[str], d.pop("platform_tools", UNSET))

        prompt = d.pop("prompt", UNSET)

        result_topic = d.pop("result_topic", UNSET)

        role = d.pop("role", UNSET)

        secrets = cast(list[str], d.pop("secrets", UNSET))

        skills = cast(list[str], d.pop("skills", UNSET))

        system = d.pop("system", UNSET)

        timeout_seconds = d.pop("timeout_seconds", UNSET)

        def _parse_transcript_retention_days(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        transcript_retention_days = _parse_transcript_retention_days(
            d.pop("transcript_retention_days", UNSET)
        )

        agent_def_out = cls(
            name=name,
            can_invoke=can_invoke,
            concurrency=concurrency,
            description=description,
            enabled=enabled,
            entrypoints=entrypoints,
            harness_tools=harness_tools,
            model=model,
            platform_tools=platform_tools,
            prompt=prompt,
            result_topic=result_topic,
            role=role,
            secrets=secrets,
            skills=skills,
            system=system,
            timeout_seconds=timeout_seconds,
            transcript_retention_days=transcript_retention_days,
        )

        agent_def_out.additional_properties = d
        return agent_def_out

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
