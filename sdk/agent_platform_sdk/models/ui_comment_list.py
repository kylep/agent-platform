from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.ui_comment import UiComment


T = TypeVar("T", bound="UiCommentList")


@_attrs_define
class UiCommentList:
    """
    Attributes:
        comments (list[UiComment]):
        slug (str):
        document_id (None | str | Unset):
        next_before (None | str | Unset):
    """

    comments: list[UiComment]
    slug: str
    document_id: None | str | Unset = UNSET
    next_before: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        comments = []
        for comments_item_data in self.comments:
            comments_item = comments_item_data.to_dict()
            comments.append(comments_item)

        slug = self.slug

        document_id: None | str | Unset
        if isinstance(self.document_id, Unset):
            document_id = UNSET
        else:
            document_id = self.document_id

        next_before: None | str | Unset
        if isinstance(self.next_before, Unset):
            next_before = UNSET
        else:
            next_before = self.next_before

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "comments": comments,
                "slug": slug,
            }
        )
        if document_id is not UNSET:
            field_dict["document_id"] = document_id
        if next_before is not UNSET:
            field_dict["next_before"] = next_before

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.ui_comment import UiComment

        d = dict(src_dict)
        comments = []
        _comments = d.pop("comments")
        for comments_item_data in _comments:
            comments_item = UiComment.from_dict(comments_item_data)

            comments.append(comments_item)

        slug = d.pop("slug")

        def _parse_document_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        document_id = _parse_document_id(d.pop("document_id", UNSET))

        def _parse_next_before(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        next_before = _parse_next_before(d.pop("next_before", UNSET))

        ui_comment_list = cls(
            comments=comments,
            slug=slug,
            document_id=document_id,
            next_before=next_before,
        )

        ui_comment_list.additional_properties = d
        return ui_comment_list

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
