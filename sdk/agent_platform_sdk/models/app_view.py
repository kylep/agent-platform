from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="AppView")


@_attrs_define
class AppView:
    """
    Attributes:
        agent_key_role (None | str):
        api (bool):
        description (str):
        error (None | str):
        icon (str):
        kafka_topics (list[str]):
        name (str):
        postgres (bool):
        ready (bool | None):
        ready_replicas (int):
        redis (bool):
        ui (bool):
    """

    agent_key_role: None | str
    api: bool
    description: str
    error: None | str
    icon: str
    kafka_topics: list[str]
    name: str
    postgres: bool
    ready: bool | None
    ready_replicas: int
    redis: bool
    ui: bool
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        agent_key_role: None | str
        agent_key_role = self.agent_key_role

        api = self.api

        description = self.description

        error: None | str
        error = self.error

        icon = self.icon

        kafka_topics = self.kafka_topics

        name = self.name

        postgres = self.postgres

        ready: bool | None
        ready = self.ready

        ready_replicas = self.ready_replicas

        redis = self.redis

        ui = self.ui

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "agent_key_role": agent_key_role,
                "api": api,
                "description": description,
                "error": error,
                "icon": icon,
                "kafka_topics": kafka_topics,
                "name": name,
                "postgres": postgres,
                "ready": ready,
                "ready_replicas": ready_replicas,
                "redis": redis,
                "ui": ui,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)

        def _parse_agent_key_role(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        agent_key_role = _parse_agent_key_role(d.pop("agent_key_role"))

        api = d.pop("api")

        description = d.pop("description")

        def _parse_error(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        error = _parse_error(d.pop("error"))

        icon = d.pop("icon")

        kafka_topics = cast(list[str], d.pop("kafka_topics"))

        name = d.pop("name")

        postgres = d.pop("postgres")

        def _parse_ready(data: object) -> bool | None:
            if data is None:
                return data
            return cast(bool | None, data)

        ready = _parse_ready(d.pop("ready"))

        ready_replicas = d.pop("ready_replicas")

        redis = d.pop("redis")

        ui = d.pop("ui")

        app_view = cls(
            agent_key_role=agent_key_role,
            api=api,
            description=description,
            error=error,
            icon=icon,
            kafka_topics=kafka_topics,
            name=name,
            postgres=postgres,
            ready=ready,
            ready_replicas=ready_replicas,
            redis=redis,
            ui=ui,
        )

        app_view.additional_properties = d
        return app_view

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
