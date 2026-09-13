from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="WikiPromoteIn")


@_attrs_define
class WikiPromoteIn:
    """
    Attributes:
        memory_id (str):
        slug (None | str | Unset):
        title (None | str | Unset):
    """

    memory_id: str
    slug: None | str | Unset = UNSET
    title: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        memory_id = self.memory_id

        slug: None | str | Unset
        if isinstance(self.slug, Unset):
            slug = UNSET
        else:
            slug = self.slug

        title: None | str | Unset
        if isinstance(self.title, Unset):
            title = UNSET
        else:
            title = self.title

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "memory_id": memory_id,
            }
        )
        if slug is not UNSET:
            field_dict["slug"] = slug
        if title is not UNSET:
            field_dict["title"] = title

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        memory_id = d.pop("memory_id")

        def _parse_slug(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        slug = _parse_slug(d.pop("slug", UNSET))

        def _parse_title(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        title = _parse_title(d.pop("title", UNSET))

        wiki_promote_in = cls(
            memory_id=memory_id,
            slug=slug,
            title=title,
        )

        return wiki_promote_in
