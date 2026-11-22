from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.ui_doc_summary import UiDocSummary


T = TypeVar("T", bound="UiDocList")


@_attrs_define
class UiDocList:
    """
    Attributes:
        docs (list[UiDocSummary]):
        next_before (None | str | Unset):
    """

    docs: list[UiDocSummary]
    next_before: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        docs = []
        for docs_item_data in self.docs:
            docs_item = docs_item_data.to_dict()
            docs.append(docs_item)

        next_before: None | str | Unset
        if isinstance(self.next_before, Unset):
            next_before = UNSET
        else:
            next_before = self.next_before

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "docs": docs,
            }
        )
        if next_before is not UNSET:
            field_dict["next_before"] = next_before

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.ui_doc_summary import UiDocSummary

        d = dict(src_dict)
        docs = []
        _docs = d.pop("docs")
        for docs_item_data in _docs:
            docs_item = UiDocSummary.from_dict(docs_item_data)

            docs.append(docs_item)

        def _parse_next_before(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        next_before = _parse_next_before(d.pop("next_before", UNSET))

        ui_doc_list = cls(
            docs=docs,
            next_before=next_before,
        )

        ui_doc_list.additional_properties = d
        return ui_doc_list

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
