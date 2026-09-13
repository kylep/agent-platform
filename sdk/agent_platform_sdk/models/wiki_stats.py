from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.wiki_author_count import WikiAuthorCount
    from ..models.wiki_budget_view import WikiBudgetView


T = TypeVar("T", bound="WikiStats")


@_attrs_define
class WikiStats:
    """
    Attributes:
        budget (WikiBudgetView):
        edits_24h (list[WikiAuthorCount]):
        pages (int):
        stale (int):
        wanted (int):
    """

    budget: WikiBudgetView
    edits_24h: list[WikiAuthorCount]
    pages: int
    stale: int
    wanted: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        budget = self.budget.to_dict()

        edits_24h = []
        for edits_24h_item_data in self.edits_24h:
            edits_24h_item = edits_24h_item_data.to_dict()
            edits_24h.append(edits_24h_item)

        pages = self.pages

        stale = self.stale

        wanted = self.wanted

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "budget": budget,
                "edits_24h": edits_24h,
                "pages": pages,
                "stale": stale,
                "wanted": wanted,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.wiki_author_count import WikiAuthorCount
        from ..models.wiki_budget_view import WikiBudgetView

        d = dict(src_dict)
        budget = WikiBudgetView.from_dict(d.pop("budget"))

        edits_24h = []
        _edits_24h = d.pop("edits_24h")
        for edits_24h_item_data in _edits_24h:
            edits_24h_item = WikiAuthorCount.from_dict(edits_24h_item_data)

            edits_24h.append(edits_24h_item)

        pages = d.pop("pages")

        stale = d.pop("stale")

        wanted = d.pop("wanted")

        wiki_stats = cls(
            budget=budget,
            edits_24h=edits_24h,
            pages=pages,
            stale=stale,
            wanted=wanted,
        )

        wiki_stats.additional_properties = d
        return wiki_stats

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
