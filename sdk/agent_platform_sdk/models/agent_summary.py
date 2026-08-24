from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.agent_summary_entrypoints import AgentSummaryEntrypoints


T = TypeVar("T", bound="AgentSummary")


@_attrs_define
class AgentSummary:
    """A listing row: the stored definition plus the readiness only the
    platform can derive — deliberately not columns, because they are computed
    from secrets and validation, not declared.

        Attributes:
            name (str):
            blocked (bool | Unset):  Default: False.
            blocked_reason (None | str | Unset):
            can_invoke (bool | Unset):  Default: False.
            concurrency (int | Unset):  Default: 1.
            description (str | Unset):  Default: ''.
            enabled (bool | Unset):  Default: True.
            entrypoints (AgentSummaryEntrypoints | Unset):
            error (None | str | Unset):
            harness_tools (list[str] | Unset):
            model (str | Unset):  Default: ''.
            platform_tools (list[str] | Unset):
            prompt (str | Unset):  Default: ''.
            quarantined (bool | Unset):  Default: False.
            result_topic (str | Unset):  Default: ''.
            role (str | Unset):  Default: 'operator'.
            schedule (str | Unset):  Default: ''.
            secrets (list[str] | Unset):
            skills (list[str] | Unset):
            system (bool | Unset):  Default: False.
            timeout_seconds (int | Unset):  Default: 1800.
            transcript_retention_days (int | None | Unset):
    """

    name: str
    blocked: bool | Unset = False
    blocked_reason: None | str | Unset = UNSET
    can_invoke: bool | Unset = False
    concurrency: int | Unset = 1
    description: str | Unset = ""
    enabled: bool | Unset = True
    entrypoints: AgentSummaryEntrypoints | Unset = UNSET
    error: None | str | Unset = UNSET
    harness_tools: list[str] | Unset = UNSET
    model: str | Unset = ""
    platform_tools: list[str] | Unset = UNSET
    prompt: str | Unset = ""
    quarantined: bool | Unset = False
    result_topic: str | Unset = ""
    role: str | Unset = "operator"
    schedule: str | Unset = ""
    secrets: list[str] | Unset = UNSET
    skills: list[str] | Unset = UNSET
    system: bool | Unset = False
    timeout_seconds: int | Unset = 1800
    transcript_retention_days: int | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        blocked = self.blocked

        blocked_reason: None | str | Unset
        if isinstance(self.blocked_reason, Unset):
            blocked_reason = UNSET
        else:
            blocked_reason = self.blocked_reason

        can_invoke = self.can_invoke

        concurrency = self.concurrency

        description = self.description

        enabled = self.enabled

        entrypoints: dict[str, Any] | Unset = UNSET
        if not isinstance(self.entrypoints, Unset):
            entrypoints = self.entrypoints.to_dict()

        error: None | str | Unset
        if isinstance(self.error, Unset):
            error = UNSET
        else:
            error = self.error

        harness_tools: list[str] | Unset = UNSET
        if not isinstance(self.harness_tools, Unset):
            harness_tools = self.harness_tools

        model = self.model

        platform_tools: list[str] | Unset = UNSET
        if not isinstance(self.platform_tools, Unset):
            platform_tools = self.platform_tools

        prompt = self.prompt

        quarantined = self.quarantined

        result_topic = self.result_topic

        role = self.role

        schedule = self.schedule

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
        if blocked is not UNSET:
            field_dict["blocked"] = blocked
        if blocked_reason is not UNSET:
            field_dict["blocked_reason"] = blocked_reason
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
        if error is not UNSET:
            field_dict["error"] = error
        if harness_tools is not UNSET:
            field_dict["harness_tools"] = harness_tools
        if model is not UNSET:
            field_dict["model"] = model
        if platform_tools is not UNSET:
            field_dict["platform_tools"] = platform_tools
        if prompt is not UNSET:
            field_dict["prompt"] = prompt
        if quarantined is not UNSET:
            field_dict["quarantined"] = quarantined
        if result_topic is not UNSET:
            field_dict["result_topic"] = result_topic
        if role is not UNSET:
            field_dict["role"] = role
        if schedule is not UNSET:
            field_dict["schedule"] = schedule
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
        from ..models.agent_summary_entrypoints import AgentSummaryEntrypoints

        d = dict(src_dict)
        name = d.pop("name")

        blocked = d.pop("blocked", UNSET)

        def _parse_blocked_reason(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        blocked_reason = _parse_blocked_reason(d.pop("blocked_reason", UNSET))

        can_invoke = d.pop("can_invoke", UNSET)

        concurrency = d.pop("concurrency", UNSET)

        description = d.pop("description", UNSET)

        enabled = d.pop("enabled", UNSET)

        _entrypoints = d.pop("entrypoints", UNSET)
        entrypoints: AgentSummaryEntrypoints | Unset
        if isinstance(_entrypoints, Unset):
            entrypoints = UNSET
        else:
            entrypoints = AgentSummaryEntrypoints.from_dict(_entrypoints)

        def _parse_error(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        error = _parse_error(d.pop("error", UNSET))

        harness_tools = cast(list[str], d.pop("harness_tools", UNSET))

        model = d.pop("model", UNSET)

        platform_tools = cast(list[str], d.pop("platform_tools", UNSET))

        prompt = d.pop("prompt", UNSET)

        quarantined = d.pop("quarantined", UNSET)

        result_topic = d.pop("result_topic", UNSET)

        role = d.pop("role", UNSET)

        schedule = d.pop("schedule", UNSET)

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

        agent_summary = cls(
            name=name,
            blocked=blocked,
            blocked_reason=blocked_reason,
            can_invoke=can_invoke,
            concurrency=concurrency,
            description=description,
            enabled=enabled,
            entrypoints=entrypoints,
            error=error,
            harness_tools=harness_tools,
            model=model,
            platform_tools=platform_tools,
            prompt=prompt,
            quarantined=quarantined,
            result_topic=result_topic,
            role=role,
            schedule=schedule,
            secrets=secrets,
            skills=skills,
            system=system,
            timeout_seconds=timeout_seconds,
            transcript_retention_days=transcript_retention_days,
        )

        agent_summary.additional_properties = d
        return agent_summary

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
