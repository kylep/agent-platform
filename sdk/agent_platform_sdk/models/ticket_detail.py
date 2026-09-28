from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.ticket_event_view import TicketEventView
    from ..models.ticket_run_ref import TicketRunRef
    from ..models.ticket_thinking import TicketThinking
    from ..models.ticket_view import TicketView


T = TypeVar("T", bound="TicketDetail")


@_attrs_define
class TicketDetail:
    """
    Attributes:
        events (list[TicketEventView]):
        root_message_id (None | str):
        runs (list[TicketRunRef]):
        thinking (None | TicketThinking):
        ticket (TicketView):
    """

    events: list[TicketEventView]
    root_message_id: None | str
    runs: list[TicketRunRef]
    thinking: None | TicketThinking
    ticket: TicketView
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.ticket_thinking import TicketThinking

        events = []
        for events_item_data in self.events:
            events_item = events_item_data.to_dict()
            events.append(events_item)

        root_message_id: None | str
        root_message_id = self.root_message_id

        runs = []
        for runs_item_data in self.runs:
            runs_item = runs_item_data.to_dict()
            runs.append(runs_item)

        thinking: dict[str, Any] | None
        if isinstance(self.thinking, TicketThinking):
            thinking = self.thinking.to_dict()
        else:
            thinking = self.thinking

        ticket = self.ticket.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "events": events,
                "root_message_id": root_message_id,
                "runs": runs,
                "thinking": thinking,
                "ticket": ticket,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.ticket_event_view import TicketEventView
        from ..models.ticket_run_ref import TicketRunRef
        from ..models.ticket_thinking import TicketThinking
        from ..models.ticket_view import TicketView

        d = dict(src_dict)
        events = []
        _events = d.pop("events")
        for events_item_data in _events:
            events_item = TicketEventView.from_dict(events_item_data)

            events.append(events_item)

        def _parse_root_message_id(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        root_message_id = _parse_root_message_id(d.pop("root_message_id"))

        runs = []
        _runs = d.pop("runs")
        for runs_item_data in _runs:
            runs_item = TicketRunRef.from_dict(runs_item_data)

            runs.append(runs_item)

        def _parse_thinking(data: object) -> None | TicketThinking:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                thinking_type_0 = TicketThinking.from_dict(data)

                return thinking_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | TicketThinking, data)

        thinking = _parse_thinking(d.pop("thinking"))

        ticket = TicketView.from_dict(d.pop("ticket"))

        ticket_detail = cls(
            events=events,
            root_message_id=root_message_id,
            runs=runs,
            thinking=thinking,
            ticket=ticket,
        )

        ticket_detail.additional_properties = d
        return ticket_detail

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
