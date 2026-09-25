from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.entrypoints_in import EntrypointsIn


T = TypeVar("T", bound="AgentCreateIn")


@_attrs_define
class AgentCreateIn:
    """Create/import payload — same definition, but the name is the one thing
    that cannot be defaulted, plus the one knob that is about the write rather
    than about the agent.

        Attributes:
            name (str):
            agent_type (str | Unset):  Default: 'worker'.
            artifacts (bool | None | Unset):
            can_invoke (bool | Unset):  Default: False.
            concurrency (int | Unset):  Default: 1.
            description (str | Unset):  Default: ''.
            enabled (bool | Unset):  Default: True.
            entrypoints (EntrypointsIn | Unset):
            get_quota_usage (bool | None | Unset):
            harness_tools (list[str] | Unset):
            may_delete_tests (bool | Unset):  Default: False.
            memory (bool | None | Unset):
            model (str | Unset):  Default: ''.
            platform_tools (list[str] | Unset):
            prompt (str | Unset):  Default: ''.
            push_path_globs (list[str] | Unset):
            quota_5h_max_pct (int | Unset):  Default: 80.
            quota_7d_max_pct (int | Unset):  Default: 50.
            relay (bool | None | Unset):
            responds_to_all (bool | Unset):  Default: True.
            result_topic (str | Unset):  Default: ''.
            role (str | Unset):  Default: 'operator'.
            runtime (str | Unset):  Default: 'claude'.
            secrets (list[str] | Unset):
            skills (list[str] | Unset):
            system (bool | Unset):  Default: False.
            tickets (bool | None | Unset):
            timeout_seconds (int | Unset):  Default: 1800.
            transcript_retention_days (int | None | Unset):
            wiki (bool | None | Unset):
    """

    name: str
    agent_type: str | Unset = "worker"
    artifacts: bool | None | Unset = UNSET
    can_invoke: bool | Unset = False
    concurrency: int | Unset = 1
    description: str | Unset = ""
    enabled: bool | Unset = True
    entrypoints: EntrypointsIn | Unset = UNSET
    get_quota_usage: bool | None | Unset = UNSET
    harness_tools: list[str] | Unset = UNSET
    may_delete_tests: bool | Unset = False
    memory: bool | None | Unset = UNSET
    model: str | Unset = ""
    platform_tools: list[str] | Unset = UNSET
    prompt: str | Unset = ""
    push_path_globs: list[str] | Unset = UNSET
    quota_5h_max_pct: int | Unset = 80
    quota_7d_max_pct: int | Unset = 50
    relay: bool | None | Unset = UNSET
    responds_to_all: bool | Unset = True
    result_topic: str | Unset = ""
    role: str | Unset = "operator"
    runtime: str | Unset = "claude"
    secrets: list[str] | Unset = UNSET
    skills: list[str] | Unset = UNSET
    system: bool | Unset = False
    tickets: bool | None | Unset = UNSET
    timeout_seconds: int | Unset = 1800
    transcript_retention_days: int | None | Unset = UNSET
    wiki: bool | None | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        agent_type = self.agent_type

        artifacts: bool | None | Unset
        if isinstance(self.artifacts, Unset):
            artifacts = UNSET
        else:
            artifacts = self.artifacts

        can_invoke = self.can_invoke

        concurrency = self.concurrency

        description = self.description

        enabled = self.enabled

        entrypoints: dict[str, Any] | Unset = UNSET
        if not isinstance(self.entrypoints, Unset):
            entrypoints = self.entrypoints.to_dict()

        get_quota_usage: bool | None | Unset
        if isinstance(self.get_quota_usage, Unset):
            get_quota_usage = UNSET
        else:
            get_quota_usage = self.get_quota_usage

        harness_tools: list[str] | Unset = UNSET
        if not isinstance(self.harness_tools, Unset):
            harness_tools = self.harness_tools

        may_delete_tests = self.may_delete_tests

        memory: bool | None | Unset
        if isinstance(self.memory, Unset):
            memory = UNSET
        else:
            memory = self.memory

        model = self.model

        platform_tools: list[str] | Unset = UNSET
        if not isinstance(self.platform_tools, Unset):
            platform_tools = self.platform_tools

        prompt = self.prompt

        push_path_globs: list[str] | Unset = UNSET
        if not isinstance(self.push_path_globs, Unset):
            push_path_globs = self.push_path_globs

        quota_5h_max_pct = self.quota_5h_max_pct

        quota_7d_max_pct = self.quota_7d_max_pct

        relay: bool | None | Unset
        if isinstance(self.relay, Unset):
            relay = UNSET
        else:
            relay = self.relay

        responds_to_all = self.responds_to_all

        result_topic = self.result_topic

        role = self.role

        runtime = self.runtime

        secrets: list[str] | Unset = UNSET
        if not isinstance(self.secrets, Unset):
            secrets = self.secrets

        skills: list[str] | Unset = UNSET
        if not isinstance(self.skills, Unset):
            skills = self.skills

        system = self.system

        tickets: bool | None | Unset
        if isinstance(self.tickets, Unset):
            tickets = UNSET
        else:
            tickets = self.tickets

        timeout_seconds = self.timeout_seconds

        transcript_retention_days: int | None | Unset
        if isinstance(self.transcript_retention_days, Unset):
            transcript_retention_days = UNSET
        else:
            transcript_retention_days = self.transcript_retention_days

        wiki: bool | None | Unset
        if isinstance(self.wiki, Unset):
            wiki = UNSET
        else:
            wiki = self.wiki

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "name": name,
            }
        )
        if agent_type is not UNSET:
            field_dict["agent_type"] = agent_type
        if artifacts is not UNSET:
            field_dict["artifacts"] = artifacts
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
        if get_quota_usage is not UNSET:
            field_dict["get_quota_usage"] = get_quota_usage
        if harness_tools is not UNSET:
            field_dict["harness_tools"] = harness_tools
        if may_delete_tests is not UNSET:
            field_dict["may_delete_tests"] = may_delete_tests
        if memory is not UNSET:
            field_dict["memory"] = memory
        if model is not UNSET:
            field_dict["model"] = model
        if platform_tools is not UNSET:
            field_dict["platform_tools"] = platform_tools
        if prompt is not UNSET:
            field_dict["prompt"] = prompt
        if push_path_globs is not UNSET:
            field_dict["push_path_globs"] = push_path_globs
        if quota_5h_max_pct is not UNSET:
            field_dict["quota_5h_max_pct"] = quota_5h_max_pct
        if quota_7d_max_pct is not UNSET:
            field_dict["quota_7d_max_pct"] = quota_7d_max_pct
        if relay is not UNSET:
            field_dict["relay"] = relay
        if responds_to_all is not UNSET:
            field_dict["responds_to_all"] = responds_to_all
        if result_topic is not UNSET:
            field_dict["result_topic"] = result_topic
        if role is not UNSET:
            field_dict["role"] = role
        if runtime is not UNSET:
            field_dict["runtime"] = runtime
        if secrets is not UNSET:
            field_dict["secrets"] = secrets
        if skills is not UNSET:
            field_dict["skills"] = skills
        if system is not UNSET:
            field_dict["system"] = system
        if tickets is not UNSET:
            field_dict["tickets"] = tickets
        if timeout_seconds is not UNSET:
            field_dict["timeout_seconds"] = timeout_seconds
        if transcript_retention_days is not UNSET:
            field_dict["transcript_retention_days"] = transcript_retention_days
        if wiki is not UNSET:
            field_dict["wiki"] = wiki

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.entrypoints_in import EntrypointsIn

        d = dict(src_dict)
        name = d.pop("name")

        agent_type = d.pop("agent_type", UNSET)

        def _parse_artifacts(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        artifacts = _parse_artifacts(d.pop("artifacts", UNSET))

        can_invoke = d.pop("can_invoke", UNSET)

        concurrency = d.pop("concurrency", UNSET)

        description = d.pop("description", UNSET)

        enabled = d.pop("enabled", UNSET)

        _entrypoints = d.pop("entrypoints", UNSET)
        entrypoints: EntrypointsIn | Unset
        if isinstance(_entrypoints, Unset):
            entrypoints = UNSET
        else:
            entrypoints = EntrypointsIn.from_dict(_entrypoints)

        def _parse_get_quota_usage(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        get_quota_usage = _parse_get_quota_usage(d.pop("get_quota_usage", UNSET))

        harness_tools = cast(list[str], d.pop("harness_tools", UNSET))

        may_delete_tests = d.pop("may_delete_tests", UNSET)

        def _parse_memory(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        memory = _parse_memory(d.pop("memory", UNSET))

        model = d.pop("model", UNSET)

        platform_tools = cast(list[str], d.pop("platform_tools", UNSET))

        prompt = d.pop("prompt", UNSET)

        push_path_globs = cast(list[str], d.pop("push_path_globs", UNSET))

        quota_5h_max_pct = d.pop("quota_5h_max_pct", UNSET)

        quota_7d_max_pct = d.pop("quota_7d_max_pct", UNSET)

        def _parse_relay(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        relay = _parse_relay(d.pop("relay", UNSET))

        responds_to_all = d.pop("responds_to_all", UNSET)

        result_topic = d.pop("result_topic", UNSET)

        role = d.pop("role", UNSET)

        runtime = d.pop("runtime", UNSET)

        secrets = cast(list[str], d.pop("secrets", UNSET))

        skills = cast(list[str], d.pop("skills", UNSET))

        system = d.pop("system", UNSET)

        def _parse_tickets(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        tickets = _parse_tickets(d.pop("tickets", UNSET))

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

        def _parse_wiki(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        wiki = _parse_wiki(d.pop("wiki", UNSET))

        agent_create_in = cls(
            name=name,
            agent_type=agent_type,
            artifacts=artifacts,
            can_invoke=can_invoke,
            concurrency=concurrency,
            description=description,
            enabled=enabled,
            entrypoints=entrypoints,
            get_quota_usage=get_quota_usage,
            harness_tools=harness_tools,
            may_delete_tests=may_delete_tests,
            memory=memory,
            model=model,
            platform_tools=platform_tools,
            prompt=prompt,
            push_path_globs=push_path_globs,
            quota_5h_max_pct=quota_5h_max_pct,
            quota_7d_max_pct=quota_7d_max_pct,
            relay=relay,
            responds_to_all=responds_to_all,
            result_topic=result_topic,
            role=role,
            runtime=runtime,
            secrets=secrets,
            skills=skills,
            system=system,
            tickets=tickets,
            timeout_seconds=timeout_seconds,
            transcript_retention_days=transcript_retention_days,
            wiki=wiki,
        )

        return agent_create_in
