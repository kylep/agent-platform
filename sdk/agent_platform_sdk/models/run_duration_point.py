from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="RunDurationPoint")


@_attrs_define
class RunDurationPoint:
    """
    Attributes:
        agent (str):
        finished_at (str):
        run_id (str):
        seconds (float):
        state (str):
    """

    agent: str
    finished_at: str
    run_id: str
    seconds: float
    state: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        agent = self.agent

        finished_at = self.finished_at

        run_id = self.run_id

        seconds = self.seconds

        state = self.state

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "agent": agent,
                "finished_at": finished_at,
                "run_id": run_id,
                "seconds": seconds,
                "state": state,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        agent = d.pop("agent")

        finished_at = d.pop("finished_at")

        run_id = d.pop("run_id")

        seconds = d.pop("seconds")

        state = d.pop("state")

        run_duration_point = cls(
            agent=agent,
            finished_at=finished_at,
            run_id=run_id,
            seconds=seconds,
            state=state,
        )

        run_duration_point.additional_properties = d
        return run_duration_point

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
