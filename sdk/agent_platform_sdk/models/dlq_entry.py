from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="DlqEntry")


@_attrs_define
class DlqEntry:
    """
    Attributes:
        agent (str):
        created_at (None | str):
        error (None | str):
        finished_at (None | str):
        id (str):
        trigger (str):
    """

    agent: str
    created_at: None | str
    error: None | str
    finished_at: None | str
    id: str
    trigger: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        agent = self.agent

        created_at: None | str
        created_at = self.created_at

        error: None | str
        error = self.error

        finished_at: None | str
        finished_at = self.finished_at

        id = self.id

        trigger = self.trigger

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "agent": agent,
                "created_at": created_at,
                "error": error,
                "finished_at": finished_at,
                "id": id,
                "trigger": trigger,
            }
        )

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

        def _parse_error(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        error = _parse_error(d.pop("error"))

        def _parse_finished_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        finished_at = _parse_finished_at(d.pop("finished_at"))

        id = d.pop("id")

        trigger = d.pop("trigger")

        dlq_entry = cls(
            agent=agent,
            created_at=created_at,
            error=error,
            finished_at=finished_at,
            id=id,
            trigger=trigger,
        )

        dlq_entry.additional_properties = d
        return dlq_entry

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
