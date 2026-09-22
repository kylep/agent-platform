from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.ticket_actor_count import TicketActorCount
    from ..models.ticket_budget_view import TicketBudgetView


T = TypeVar("T", bound="TicketStats")


@_attrs_define
class TicketStats:
    """
    Attributes:
        blocked (int):
        budget (TicketBudgetView):
        done_24h (int):
        in_progress (int):
        moved_24h (list[TicketActorCount]):
        open_ (int):
        orphaned (int):
        review (int):
        stale (int):
    """

    blocked: int
    budget: TicketBudgetView
    done_24h: int
    in_progress: int
    moved_24h: list[TicketActorCount]
    open_: int
    orphaned: int
    review: int
    stale: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        blocked = self.blocked

        budget = self.budget.to_dict()

        done_24h = self.done_24h

        in_progress = self.in_progress

        moved_24h = []
        for moved_24h_item_data in self.moved_24h:
            moved_24h_item = moved_24h_item_data.to_dict()
            moved_24h.append(moved_24h_item)

        open_ = self.open_

        orphaned = self.orphaned

        review = self.review

        stale = self.stale

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "blocked": blocked,
                "budget": budget,
                "done_24h": done_24h,
                "in_progress": in_progress,
                "moved_24h": moved_24h,
                "open": open_,
                "orphaned": orphaned,
                "review": review,
                "stale": stale,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.ticket_actor_count import TicketActorCount
        from ..models.ticket_budget_view import TicketBudgetView

        d = dict(src_dict)
        blocked = d.pop("blocked")

        budget = TicketBudgetView.from_dict(d.pop("budget"))

        done_24h = d.pop("done_24h")

        in_progress = d.pop("in_progress")

        moved_24h = []
        _moved_24h = d.pop("moved_24h")
        for moved_24h_item_data in _moved_24h:
            moved_24h_item = TicketActorCount.from_dict(moved_24h_item_data)

            moved_24h.append(moved_24h_item)

        open_ = d.pop("open")

        orphaned = d.pop("orphaned")

        review = d.pop("review")

        stale = d.pop("stale")

        ticket_stats = cls(
            blocked=blocked,
            budget=budget,
            done_24h=done_24h,
            in_progress=in_progress,
            moved_24h=moved_24h,
            open_=open_,
            orphaned=orphaned,
            review=review,
            stale=stale,
        )

        ticket_stats.additional_properties = d
        return ticket_stats

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
