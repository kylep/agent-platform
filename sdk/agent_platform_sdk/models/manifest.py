from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="Manifest")


@_attrs_define
class Manifest:
    """
    Attributes:
        can_invoke (bool | Unset):  Default: False.
        concurrency (int | Unset):  Default: 1.
        description (str | Unset):  Default: ''.
        memory (bool | Unset):  Default: False.
        model (str | Unset):  Default: ''.
        role (str | Unset):  Default: 'operator'.
        schedule (str | Unset):  Default: ''.
        secrets (list[str] | Unset):
        skills (list[str] | Unset):
        system (bool | Unset):  Default: False.
        timeout_seconds (int | Unset):  Default: 1800.
        transcript_retention_days (int | None | Unset):
    """

    can_invoke: bool | Unset = False
    concurrency: int | Unset = 1
    description: str | Unset = ""
    memory: bool | Unset = False
    model: str | Unset = ""
    role: str | Unset = "operator"
    schedule: str | Unset = ""
    secrets: list[str] | Unset = UNSET
    skills: list[str] | Unset = UNSET
    system: bool | Unset = False
    timeout_seconds: int | Unset = 1800
    transcript_retention_days: int | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        can_invoke = self.can_invoke

        concurrency = self.concurrency

        description = self.description

        memory = self.memory

        model = self.model

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
        field_dict.update({})
        if can_invoke is not UNSET:
            field_dict["can_invoke"] = can_invoke
        if concurrency is not UNSET:
            field_dict["concurrency"] = concurrency
        if description is not UNSET:
            field_dict["description"] = description
        if memory is not UNSET:
            field_dict["memory"] = memory
        if model is not UNSET:
            field_dict["model"] = model
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
        d = dict(src_dict)
        can_invoke = d.pop("can_invoke", UNSET)

        concurrency = d.pop("concurrency", UNSET)

        description = d.pop("description", UNSET)

        memory = d.pop("memory", UNSET)

        model = d.pop("model", UNSET)

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

        manifest = cls(
            can_invoke=can_invoke,
            concurrency=concurrency,
            description=description,
            memory=memory,
            model=model,
            role=role,
            schedule=schedule,
            secrets=secrets,
            skills=skills,
            system=system,
            timeout_seconds=timeout_seconds,
            transcript_retention_days=transcript_retention_days,
        )

        manifest.additional_properties = d
        return manifest

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
