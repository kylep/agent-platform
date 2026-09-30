from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="TaskIn")


@_attrs_define
class TaskIn:
    """
    Attributes:
        agent (str):
        prompt (str):
        delay_minutes (int | None | Unset):
        delivery (str | Unset):  Default: 'log'.
        idempotency_key (None | str | Unset):
        late_minutes (int | Unset):  Default: 60.
        model (str | Unset):  Default: ''.
        run_at (datetime.datetime | None | Unset):
        source_conversation_id (None | str | Unset):
        source_message_id (None | str | Unset):
        timezone (str | Unset):  Default: 'UTC'.
        title (str | Unset):  Default: 'One-time run'.
    """

    agent: str
    prompt: str
    delay_minutes: int | None | Unset = UNSET
    delivery: str | Unset = "log"
    idempotency_key: None | str | Unset = UNSET
    late_minutes: int | Unset = 60
    model: str | Unset = ""
    run_at: datetime.datetime | None | Unset = UNSET
    source_conversation_id: None | str | Unset = UNSET
    source_message_id: None | str | Unset = UNSET
    timezone: str | Unset = "UTC"
    title: str | Unset = "One-time run"

    def to_dict(self) -> dict[str, Any]:
        agent = self.agent

        prompt = self.prompt

        delay_minutes: int | None | Unset
        if isinstance(self.delay_minutes, Unset):
            delay_minutes = UNSET
        else:
            delay_minutes = self.delay_minutes

        delivery = self.delivery

        idempotency_key: None | str | Unset
        if isinstance(self.idempotency_key, Unset):
            idempotency_key = UNSET
        else:
            idempotency_key = self.idempotency_key

        late_minutes = self.late_minutes

        model = self.model

        run_at: None | str | Unset
        if isinstance(self.run_at, Unset):
            run_at = UNSET
        elif isinstance(self.run_at, datetime.datetime):
            run_at = self.run_at.isoformat()
        else:
            run_at = self.run_at

        source_conversation_id: None | str | Unset
        if isinstance(self.source_conversation_id, Unset):
            source_conversation_id = UNSET
        else:
            source_conversation_id = self.source_conversation_id

        source_message_id: None | str | Unset
        if isinstance(self.source_message_id, Unset):
            source_message_id = UNSET
        else:
            source_message_id = self.source_message_id

        timezone = self.timezone

        title = self.title

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "agent": agent,
                "prompt": prompt,
            }
        )
        if delay_minutes is not UNSET:
            field_dict["delay_minutes"] = delay_minutes
        if delivery is not UNSET:
            field_dict["delivery"] = delivery
        if idempotency_key is not UNSET:
            field_dict["idempotency_key"] = idempotency_key
        if late_minutes is not UNSET:
            field_dict["late_minutes"] = late_minutes
        if model is not UNSET:
            field_dict["model"] = model
        if run_at is not UNSET:
            field_dict["run_at"] = run_at
        if source_conversation_id is not UNSET:
            field_dict["source_conversation_id"] = source_conversation_id
        if source_message_id is not UNSET:
            field_dict["source_message_id"] = source_message_id
        if timezone is not UNSET:
            field_dict["timezone"] = timezone
        if title is not UNSET:
            field_dict["title"] = title

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        agent = d.pop("agent")

        prompt = d.pop("prompt")

        def _parse_delay_minutes(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        delay_minutes = _parse_delay_minutes(d.pop("delay_minutes", UNSET))

        delivery = d.pop("delivery", UNSET)

        def _parse_idempotency_key(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        idempotency_key = _parse_idempotency_key(d.pop("idempotency_key", UNSET))

        late_minutes = d.pop("late_minutes", UNSET)

        model = d.pop("model", UNSET)

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

        def _parse_source_conversation_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        source_conversation_id = _parse_source_conversation_id(
            d.pop("source_conversation_id", UNSET)
        )

        def _parse_source_message_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        source_message_id = _parse_source_message_id(d.pop("source_message_id", UNSET))

        timezone = d.pop("timezone", UNSET)

        title = d.pop("title", UNSET)

        task_in = cls(
            agent=agent,
            prompt=prompt,
            delay_minutes=delay_minutes,
            delivery=delivery,
            idempotency_key=idempotency_key,
            late_minutes=late_minutes,
            model=model,
            run_at=run_at,
            source_conversation_id=source_conversation_id,
            source_message_id=source_message_id,
            timezone=timezone,
            title=title,
        )

        return task_in
