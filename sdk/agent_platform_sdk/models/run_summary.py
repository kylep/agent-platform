from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="RunSummary")


@_attrs_define
class RunSummary:
    """
    Attributes:
        agent (str):
        created_at (None | str):
        id (str):
        state (str):
        summary (None | str):
        tags (list[str]):
        trigger (str):
        ticket_id (None | str | Unset):
    """

    agent: str
    created_at: None | str
    id: str
    state: str
    summary: None | str
    tags: list[str]
    trigger: str
    ticket_id: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        agent = self.agent

        created_at: None | str
        created_at = self.created_at

        id = self.id

        state = self.state

        summary: None | str
        summary = self.summary

        tags = self.tags

        trigger = self.trigger

        ticket_id: None | str | Unset
        if isinstance(self.ticket_id, Unset):
            ticket_id = UNSET
        else:
            ticket_id = self.ticket_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "agent": agent,
                "created_at": created_at,
                "id": id,
                "state": state,
                "summary": summary,
                "tags": tags,
                "trigger": trigger,
            }
        )
        if ticket_id is not UNSET:
            field_dict["ticket_id"] = ticket_id

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        agent = d.pop("agent")

        def _parse_created_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        created_at = _parse_created_at(d.pop("created_at"))

        id = d.pop("id")

        state = d.pop("state")

        def _parse_summary(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        summary = _parse_summary(d.pop("summary"))

        tags = cast(list[str], d.pop("tags"))

        trigger = d.pop("trigger")

        def _parse_ticket_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        ticket_id = _parse_ticket_id(d.pop("ticket_id", UNSET))

        run_summary = cls(
            agent=agent,
            created_at=created_at,
            id=id,
            state=state,
            summary=summary,
            tags=tags,
            trigger=trigger,
            ticket_id=ticket_id,
        )

        run_summary.additional_properties = d
        return run_summary

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
