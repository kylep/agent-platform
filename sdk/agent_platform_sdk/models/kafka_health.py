from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.backlog import Backlog


T = TypeVar("T", bound="KafkaHealth")


@_attrs_define
class KafkaHealth:
    """
    Attributes:
        backlog (Backlog):
        error (None | str):
        lag (int | None):
        missing_topics (list[str]):
        reachable (bool):
        topics (list[str]):
    """

    backlog: Backlog
    error: None | str
    lag: int | None
    missing_topics: list[str]
    reachable: bool
    topics: list[str]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        backlog = self.backlog.to_dict()

        error: None | str
        error = self.error

        lag: int | None
        lag = self.lag

        missing_topics = self.missing_topics

        reachable = self.reachable

        topics = self.topics

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "backlog": backlog,
                "error": error,
                "lag": lag,
                "missing_topics": missing_topics,
                "reachable": reachable,
                "topics": topics,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.backlog import Backlog

        d = dict(src_dict)
        backlog = Backlog.from_dict(d.pop("backlog"))

        def _parse_error(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        error = _parse_error(d.pop("error"))

        def _parse_lag(data: object) -> int | None:
            if data is None:
                return data
            return cast(int | None, data)

        lag = _parse_lag(d.pop("lag"))

        missing_topics = cast(list[str], d.pop("missing_topics"))

        reachable = d.pop("reachable")

        topics = cast(list[str], d.pop("topics"))

        kafka_health = cls(
            backlog=backlog,
            error=error,
            lag=lag,
            missing_topics=missing_topics,
            reachable=reachable,
            topics=topics,
        )

        kafka_health.additional_properties = d
        return kafka_health

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
