from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.run_agent_def_open_commitments_item import (
        RunAgentDefOpenCommitmentsItem,
    )
    from ..models.run_agent_def_startup_memory_type_0 import (
        RunAgentDefStartupMemoryType0,
    )


T = TypeVar("T", bound="RunAgentDef")


@_attrs_define
class RunAgentDef:
    """An agent definition as the RUN POD needs it (docs/design/15).

    Not the full row: only what the pod materializes into
    `~/.claude/agents/<name>.md` and derives its permission flags from. The two
    grant lists are EXPLICIT — an empty list means no tools, never "everything"
    — because the runner turns them straight into the file's `tools:` line.

    `description` is here because the CLI requires it: a subagent file carrying
    a `name` but no `description` is SKIPPED (silently, bar a debug-log line),
    and `claude --agent <name>` then reports the agent as not found. It is the
    row's description, not decoration.

        Attributes:
            name (str):
            prompt (str):
            description (str | Unset):  Default: ''.
            fallback_notice (str | Unset):  Default: ''.
            fallback_used (bool | Unset):  Default: False.
            harness_tools (list[str] | Unset):
            model (str | Unset):  Default: ''.
            open_commitments (list[RunAgentDefOpenCommitmentsItem] | Unset):
            platform_tools (list[str] | Unset):
            runtime (str | Unset):  Default: 'claude'.
            skills (list[str] | Unset):
            startup_memory (None | RunAgentDefStartupMemoryType0 | Unset):
    """

    name: str
    prompt: str
    description: str | Unset = ""
    fallback_notice: str | Unset = ""
    fallback_used: bool | Unset = False
    harness_tools: list[str] | Unset = UNSET
    model: str | Unset = ""
    open_commitments: list[RunAgentDefOpenCommitmentsItem] | Unset = UNSET
    platform_tools: list[str] | Unset = UNSET
    runtime: str | Unset = "claude"
    skills: list[str] | Unset = UNSET
    startup_memory: None | RunAgentDefStartupMemoryType0 | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.run_agent_def_startup_memory_type_0 import (
            RunAgentDefStartupMemoryType0,
        )

        name = self.name

        prompt = self.prompt

        description = self.description

        fallback_notice = self.fallback_notice

        fallback_used = self.fallback_used

        harness_tools: list[str] | Unset = UNSET
        if not isinstance(self.harness_tools, Unset):
            harness_tools = self.harness_tools

        model = self.model

        open_commitments: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.open_commitments, Unset):
            open_commitments = []
            for open_commitments_item_data in self.open_commitments:
                open_commitments_item = open_commitments_item_data.to_dict()
                open_commitments.append(open_commitments_item)

        platform_tools: list[str] | Unset = UNSET
        if not isinstance(self.platform_tools, Unset):
            platform_tools = self.platform_tools

        runtime = self.runtime

        skills: list[str] | Unset = UNSET
        if not isinstance(self.skills, Unset):
            skills = self.skills

        startup_memory: dict[str, Any] | None | Unset
        if isinstance(self.startup_memory, Unset):
            startup_memory = UNSET
        elif isinstance(self.startup_memory, RunAgentDefStartupMemoryType0):
            startup_memory = self.startup_memory.to_dict()
        else:
            startup_memory = self.startup_memory

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "name": name,
                "prompt": prompt,
            }
        )
        if description is not UNSET:
            field_dict["description"] = description
        if fallback_notice is not UNSET:
            field_dict["fallback_notice"] = fallback_notice
        if fallback_used is not UNSET:
            field_dict["fallback_used"] = fallback_used
        if harness_tools is not UNSET:
            field_dict["harness_tools"] = harness_tools
        if model is not UNSET:
            field_dict["model"] = model
        if open_commitments is not UNSET:
            field_dict["open_commitments"] = open_commitments
        if platform_tools is not UNSET:
            field_dict["platform_tools"] = platform_tools
        if runtime is not UNSET:
            field_dict["runtime"] = runtime
        if skills is not UNSET:
            field_dict["skills"] = skills
        if startup_memory is not UNSET:
            field_dict["startup_memory"] = startup_memory

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.run_agent_def_open_commitments_item import (
            RunAgentDefOpenCommitmentsItem,
        )
        from ..models.run_agent_def_startup_memory_type_0 import (
            RunAgentDefStartupMemoryType0,
        )

        d = dict(src_dict)
        name = d.pop("name")

        prompt = d.pop("prompt")

        description = d.pop("description", UNSET)

        fallback_notice = d.pop("fallback_notice", UNSET)

        fallback_used = d.pop("fallback_used", UNSET)

        harness_tools = cast(list[str], d.pop("harness_tools", UNSET))

        model = d.pop("model", UNSET)

        _open_commitments = d.pop("open_commitments", UNSET)
        open_commitments: list[RunAgentDefOpenCommitmentsItem] | Unset = UNSET
        if _open_commitments is not UNSET:
            open_commitments = []
            for open_commitments_item_data in _open_commitments:
                open_commitments_item = RunAgentDefOpenCommitmentsItem.from_dict(
                    open_commitments_item_data
                )

                open_commitments.append(open_commitments_item)

        platform_tools = cast(list[str], d.pop("platform_tools", UNSET))

        runtime = d.pop("runtime", UNSET)

        skills = cast(list[str], d.pop("skills", UNSET))

        def _parse_startup_memory(
            data: object,
        ) -> None | RunAgentDefStartupMemoryType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                startup_memory_type_0 = RunAgentDefStartupMemoryType0.from_dict(data)

                return startup_memory_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | RunAgentDefStartupMemoryType0 | Unset, data)

        startup_memory = _parse_startup_memory(d.pop("startup_memory", UNSET))

        run_agent_def = cls(
            name=name,
            prompt=prompt,
            description=description,
            fallback_notice=fallback_notice,
            fallback_used=fallback_used,
            harness_tools=harness_tools,
            model=model,
            open_commitments=open_commitments,
            platform_tools=platform_tools,
            runtime=runtime,
            skills=skills,
            startup_memory=startup_memory,
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
