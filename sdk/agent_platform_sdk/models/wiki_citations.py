from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.wiki_citation import WikiCitation


T = TypeVar("T", bound="WikiCitations")


@_attrs_define
class WikiCitations:
    """
    Attributes:
        count (int):
        count_capped (bool | Unset):  Default: False.
        last (list[WikiCitation] | Unset):
    """

    count: int
    count_capped: bool | Unset = False
    last: list[WikiCitation] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        count = self.count

        count_capped = self.count_capped

        last: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.last, Unset):
            last = []
            for last_item_data in self.last:
                last_item = last_item_data.to_dict()
                last.append(last_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "count": count,
            }
        )
        if count_capped is not UNSET:
            field_dict["count_capped"] = count_capped
        if last is not UNSET:
            field_dict["last"] = last

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.wiki_citation import WikiCitation

        d = dict(src_dict)
        count = d.pop("count")

        count_capped = d.pop("count_capped", UNSET)

        _last = d.pop("last", UNSET)
        last: list[WikiCitation] | Unset = UNSET
        if _last is not UNSET:
            last = []
            for last_item_data in _last:
                last_item = WikiCitation.from_dict(last_item_data)

                last.append(last_item)

        wiki_citations = cls(
            count=count,
            count_capped=count_capped,
            last=last,
        )

        wiki_citations.additional_properties = d
        return wiki_citations

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
