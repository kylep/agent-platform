from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="TicketAssignIn")


@_attrs_define
class TicketAssignIn:
    """
    Attributes:
        to (None | str):
        notify (bool | Unset):  Default: True.
        reason (None | str | Unset):
    """

    to: None | str
    notify: bool | Unset = True
    reason: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        to: None | str
        to = self.to

        notify = self.notify

        reason: None | str | Unset
        if isinstance(self.reason, Unset):
            reason = UNSET
        else:
            reason = self.reason

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "to": to,
            }
        )
        if notify is not UNSET:
            field_dict["notify"] = notify
        if reason is not UNSET:
            field_dict["reason"] = reason

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)

        def _parse_to(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        to = _parse_to(d.pop("to"))

        notify = d.pop("notify", UNSET)

        def _parse_reason(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        reason = _parse_reason(d.pop("reason", UNSET))

        ticket_assign_in = cls(
            to=to,
            notify=notify,
            reason=reason,
        )

        return ticket_assign_in
