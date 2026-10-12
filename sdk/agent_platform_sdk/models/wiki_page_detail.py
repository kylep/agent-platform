from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.wiki_citations import WikiCitations
    from ..models.wiki_page_ref import WikiPageRef
    from ..models.wiki_page_view import WikiPageView


T = TypeVar("T", bound="WikiPageDetail")


@_attrs_define
class WikiPageDetail:
    """
    Attributes:
        backlinks (list[WikiPageRef]):
        cited_in (WikiCitations):
        page (WikiPageView):
        ancestors (list[WikiPageRef] | Unset):
        children (list[WikiPageRef] | Unset):
    """

    backlinks: list[WikiPageRef]
    cited_in: WikiCitations
    page: WikiPageView
    ancestors: list[WikiPageRef] | Unset = UNSET
    children: list[WikiPageRef] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        backlinks = []
        for backlinks_item_data in self.backlinks:
            backlinks_item = backlinks_item_data.to_dict()
            backlinks.append(backlinks_item)

        cited_in = self.cited_in.to_dict()

        page = self.page.to_dict()

        ancestors: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.ancestors, Unset):
            ancestors = []
            for ancestors_item_data in self.ancestors:
                ancestors_item = ancestors_item_data.to_dict()
                ancestors.append(ancestors_item)

        children: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.children, Unset):
            children = []
            for children_item_data in self.children:
                children_item = children_item_data.to_dict()
                children.append(children_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "backlinks": backlinks,
                "cited_in": cited_in,
                "page": page,
            }
        )
        if ancestors is not UNSET:
            field_dict["ancestors"] = ancestors
        if children is not UNSET:
            field_dict["children"] = children

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.wiki_citations import WikiCitations
        from ..models.wiki_page_ref import WikiPageRef
        from ..models.wiki_page_view import WikiPageView

        d = dict(src_dict)
        backlinks = []
        _backlinks = d.pop("backlinks")
        for backlinks_item_data in _backlinks:
            backlinks_item = WikiPageRef.from_dict(backlinks_item_data)

            backlinks.append(backlinks_item)

        cited_in = WikiCitations.from_dict(d.pop("cited_in"))

        page = WikiPageView.from_dict(d.pop("page"))

        _ancestors = d.pop("ancestors", UNSET)
        ancestors: list[WikiPageRef] | Unset = UNSET
        if _ancestors is not UNSET:
            ancestors = []
            for ancestors_item_data in _ancestors:
                ancestors_item = WikiPageRef.from_dict(ancestors_item_data)

                ancestors.append(ancestors_item)

        _children = d.pop("children", UNSET)
        children: list[WikiPageRef] | Unset = UNSET
        if _children is not UNSET:
            children = []
            for children_item_data in _children:
                children_item = WikiPageRef.from_dict(children_item_data)

                children.append(children_item)

        wiki_page_detail = cls(
            backlinks=backlinks,
            cited_in=cited_in,
            page=page,
            ancestors=ancestors,
            children=children,
        )

        wiki_page_detail.additional_properties = d
        return wiki_page_detail

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
