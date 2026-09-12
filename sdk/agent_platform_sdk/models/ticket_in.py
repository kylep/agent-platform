from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="TicketIn")


@_attrs_define
class TicketIn:
    """
    Attributes:
        channel (str):
        title (str):
        assignee (None | str | Unset):
        body (str | Unset):  Default: ''.
        due_at (datetime.datetime | None | Unset):
        labels (list[str] | Unset):
        notify (bool | Unset):  Default: True.
        parent (None | str | Unset):
        priority (str | Unset):  Default: 'p2'.
    """

    channel: str
    title: str
    assignee: None | str | Unset = UNSET
    body: str | Unset = ""
    due_at: datetime.datetime | None | Unset = UNSET
    labels: list[str] | Unset = UNSET
    notify: bool | Unset = True
    parent: None | str | Unset = UNSET
    priority: str | Unset = "p2"

    def to_dict(self) -> dict[str, Any]:
        channel = self.channel

        title = self.title

        assignee: None | str | Unset
        if isinstance(self.assignee, Unset):
            assignee = UNSET
        else:
            assignee = self.assignee

        body = self.body

        due_at: None | str | Unset
        if isinstance(self.due_at, Unset):
            due_at = UNSET
        elif isinstance(self.due_at, datetime.datetime):
            due_at = self.due_at.isoformat()
        else:
            due_at = self.due_at

        labels: list[str] | Unset = UNSET
        if not isinstance(self.labels, Unset):
            labels = self.labels

        notify = self.notify

        parent: None | str | Unset
        if isinstance(self.parent, Unset):
            parent = UNSET
        else:
            parent = self.parent

        priority = self.priority

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "channel": channel,
                "title": title,
            }
        )
        if assignee is not UNSET:
            field_dict["assignee"] = assignee
        if body is not UNSET:
            field_dict["body"] = body
        if due_at is not UNSET:
            field_dict["due_at"] = due_at
        if labels is not UNSET:
            field_dict["labels"] = labels
        if notify is not UNSET:
            field_dict["notify"] = notify
        if parent is not UNSET:
            field_dict["parent"] = parent
        if priority is not UNSET:
            field_dict["priority"] = priority

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        channel = d.pop("channel")

        title = d.pop("title")

        def _parse_assignee(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        assignee = _parse_assignee(d.pop("assignee", UNSET))

        body = d.pop("body", UNSET)

        def _parse_due_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                due_at_type_0 = datetime.datetime.fromisoformat(data)

                return due_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        due_at = _parse_due_at(d.pop("due_at", UNSET))

        labels = cast(list[str], d.pop("labels", UNSET))

        notify = d.pop("notify", UNSET)

        def _parse_parent(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        parent = _parse_parent(d.pop("parent", UNSET))

        priority = d.pop("priority", UNSET)

        ticket_in = cls(
            channel=channel,
            title=title,
            assignee=assignee,
            body=body,
            due_at=due_at,
            labels=labels,
            notify=notify,
            parent=parent,
            priority=priority,
        )

        return ticket_in
