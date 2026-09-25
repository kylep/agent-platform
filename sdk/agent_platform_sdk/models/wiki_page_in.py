from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="WikiPageIn")


@_attrs_define
class WikiPageIn:
    """
    Attributes:
        slug (str):
        title (str):
        body (str | Unset):  Default: ''.
        reason (str | Unset):  Default: ''.
        tags (list[str] | Unset):
    """

    slug: str
    title: str
    body: str | Unset = ""
    reason: str | Unset = ""
    tags: list[str] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        slug = self.slug

        title = self.title

        body = self.body

        reason = self.reason

        tags: list[str] | Unset = UNSET
        if not isinstance(self.tags, Unset):
            tags = self.tags

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "slug": slug,
                "title": title,
            }
        )
        if body is not UNSET:
            field_dict["body"] = body
        if reason is not UNSET:
            field_dict["reason"] = reason
        if tags is not UNSET:
            field_dict["tags"] = tags

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        slug = d.pop("slug")

        title = d.pop("title")

        body = d.pop("body", UNSET)

        reason = d.pop("reason", UNSET)

        tags = cast(list[str], d.pop("tags", UNSET))

        wiki_page_in = cls(
            slug=slug,
            title=title,
            body=body,
            reason=reason,
            tags=tags,
        )

        return wiki_page_in
