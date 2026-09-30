from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="TaskPatch")


@_attrs_define
class TaskPatch:
    """
    Attributes:
        version (int):
        late_minutes (int | None | Unset):
        model (None | str | Unset):
        prompt (None | str | Unset):
        run_at (datetime.datetime | None | Unset):
        timezone (None | str | Unset):
        title (None | str | Unset):
    """

    version: int
    late_minutes: int | None | Unset = UNSET
    model: None | str | Unset = UNSET
    prompt: None | str | Unset = UNSET
    run_at: datetime.datetime | None | Unset = UNSET
    timezone: None | str | Unset = UNSET
    title: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        version = self.version

        late_minutes: int | None | Unset
        if isinstance(self.late_minutes, Unset):
            late_minutes = UNSET
        else:
            late_minutes = self.late_minutes

        model: None | str | Unset
        if isinstance(self.model, Unset):
            model = UNSET
        else:
            model = self.model

        prompt: None | str | Unset
        if isinstance(self.prompt, Unset):
            prompt = UNSET
        else:
            prompt = self.prompt

        run_at: None | str | Unset
        if isinstance(self.run_at, Unset):
            run_at = UNSET
        elif isinstance(self.run_at, datetime.datetime):
            run_at = self.run_at.isoformat()
        else:
            run_at = self.run_at

        timezone: None | str | Unset
        if isinstance(self.timezone, Unset):
            timezone = UNSET
        else:
            timezone = self.timezone

        title: None | str | Unset
        if isinstance(self.title, Unset):
            title = UNSET
        else:
            title = self.title

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "version": version,
            }
        )
        if late_minutes is not UNSET:
            field_dict["late_minutes"] = late_minutes
        if model is not UNSET:
            field_dict["model"] = model
        if prompt is not UNSET:
            field_dict["prompt"] = prompt
        if run_at is not UNSET:
            field_dict["run_at"] = run_at
        if timezone is not UNSET:
            field_dict["timezone"] = timezone
        if title is not UNSET:
            field_dict["title"] = title

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        version = d.pop("version")

        def _parse_late_minutes(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        late_minutes = _parse_late_minutes(d.pop("late_minutes", UNSET))

        def _parse_model(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        model = _parse_model(d.pop("model", UNSET))

        def _parse_prompt(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        prompt = _parse_prompt(d.pop("prompt", UNSET))

        def _parse_run_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                run_at_type_0 = datetime.datetime.fromisoformat(data)

                return run_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        run_at = _parse_run_at(d.pop("run_at", UNSET))

        def _parse_timezone(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        timezone = _parse_timezone(d.pop("timezone", UNSET))

        def _parse_title(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        title = _parse_title(d.pop("title", UNSET))

        task_patch = cls(
            version=version,
            late_minutes=late_minutes,
            model=model,
            prompt=prompt,
            run_at=run_at,
            timezone=timezone,
            title=title,
        )

        return task_patch
