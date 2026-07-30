from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.run_detail_permission_denials_item import (
        RunDetailPermissionDenialsItem,
    )


T = TypeVar("T", bound="RunDetail")


@_attrs_define
class RunDetail:
    """
    Attributes:
        agent (str):
        created_at (None | str):
        depth (int):
        error (None | str):
        exit_code (int | None):
        finished_at (None | str):
        id (str):
        parent_run_id (None | str):
        permission_denials (list[RunDetailPermissionDenialsItem]):
        prompt (str):
        requested_by (str):
        secrets_granted (list[str]):
        started_at (None | str):
        state (str):
        summary (None | str):
        tags (list[str]):
        tokens_in (int):
        tokens_out (int):
        tool_calls (int):
        trigger (str):
    """

    agent: str
    created_at: None | str
    depth: int
    error: None | str
    exit_code: int | None
    finished_at: None | str
    id: str
    parent_run_id: None | str
    permission_denials: list[RunDetailPermissionDenialsItem]
    prompt: str
    requested_by: str
    secrets_granted: list[str]
    started_at: None | str
    state: str
    summary: None | str
    tags: list[str]
    tokens_in: int
    tokens_out: int
    tool_calls: int
    trigger: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        agent = self.agent

        created_at: None | str
        created_at = self.created_at

        depth = self.depth

        error: None | str
        error = self.error

        exit_code: int | None
        exit_code = self.exit_code

        finished_at: None | str
        finished_at = self.finished_at

        id = self.id

        parent_run_id: None | str
        parent_run_id = self.parent_run_id

        permission_denials = []
        for permission_denials_item_data in self.permission_denials:
            permission_denials_item = permission_denials_item_data.to_dict()
            permission_denials.append(permission_denials_item)

        prompt = self.prompt

        requested_by = self.requested_by

        secrets_granted = self.secrets_granted

        started_at: None | str
        started_at = self.started_at

        state = self.state

        summary: None | str
        summary = self.summary

        tags = self.tags

        tokens_in = self.tokens_in

        tokens_out = self.tokens_out

        tool_calls = self.tool_calls

        trigger = self.trigger

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "agent": agent,
                "created_at": created_at,
                "depth": depth,
                "error": error,
                "exit_code": exit_code,
                "finished_at": finished_at,
                "id": id,
                "parent_run_id": parent_run_id,
                "permission_denials": permission_denials,
                "prompt": prompt,
                "requested_by": requested_by,
                "secrets_granted": secrets_granted,
                "started_at": started_at,
                "state": state,
                "summary": summary,
                "tags": tags,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "tool_calls": tool_calls,
                "trigger": trigger,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.run_detail_permission_denials_item import (
            RunDetailPermissionDenialsItem,
        )

        d = dict(src_dict)
        agent = d.pop("agent")

        def _parse_created_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        created_at = _parse_created_at(d.pop("created_at"))

        depth = d.pop("depth")

        def _parse_error(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        error = _parse_error(d.pop("error"))

        def _parse_exit_code(data: object) -> int | None:
            if data is None:
                return data
            return cast(int | None, data)

        exit_code = _parse_exit_code(d.pop("exit_code"))

        def _parse_finished_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        finished_at = _parse_finished_at(d.pop("finished_at"))

        id = d.pop("id")

        def _parse_parent_run_id(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        parent_run_id = _parse_parent_run_id(d.pop("parent_run_id"))

        permission_denials = []
        _permission_denials = d.pop("permission_denials")
        for permission_denials_item_data in _permission_denials:
            permission_denials_item = RunDetailPermissionDenialsItem.from_dict(
                permission_denials_item_data
            )

            permission_denials.append(permission_denials_item)

        prompt = d.pop("prompt")

        requested_by = d.pop("requested_by")

        secrets_granted = cast(list[str], d.pop("secrets_granted"))

        def _parse_started_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        started_at = _parse_started_at(d.pop("started_at"))

        state = d.pop("state")

        def _parse_summary(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        summary = _parse_summary(d.pop("summary"))

        tags = cast(list[str], d.pop("tags"))

        tokens_in = d.pop("tokens_in")

        tokens_out = d.pop("tokens_out")

        tool_calls = d.pop("tool_calls")

        trigger = d.pop("trigger")

        run_detail = cls(
            agent=agent,
            created_at=created_at,
            depth=depth,
            error=error,
            exit_code=exit_code,
            finished_at=finished_at,
            id=id,
            parent_run_id=parent_run_id,
            permission_denials=permission_denials,
            prompt=prompt,
            requested_by=requested_by,
            secrets_granted=secrets_granted,
            started_at=started_at,
            state=state,
            summary=summary,
            tags=tags,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            tool_calls=tool_calls,
            trigger=trigger,
        )

        run_detail.additional_properties = d
        return run_detail

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
