from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="FigmaImportIn")


@_attrs_define
class FigmaImportIn:
    """
    Attributes:
        file_key (str):
        slug (str):
        node_ids (list[str] | Unset):
        pages (list[str] | Unset):
        title (str | Unset):  Default: ''.
    """

    file_key: str
    slug: str
    node_ids: list[str] | Unset = UNSET
    pages: list[str] | Unset = UNSET
    title: str | Unset = ""

    def to_dict(self) -> dict[str, Any]:
        file_key = self.file_key

        slug = self.slug

        node_ids: list[str] | Unset = UNSET
        if not isinstance(self.node_ids, Unset):
            node_ids = self.node_ids

        pages: list[str] | Unset = UNSET
        if not isinstance(self.pages, Unset):
            pages = self.pages

        title = self.title

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "file_key": file_key,
                "slug": slug,
            }
        )
        if node_ids is not UNSET:
            field_dict["node_ids"] = node_ids
        if pages is not UNSET:
            field_dict["pages"] = pages
        if title is not UNSET:
            field_dict["title"] = title

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        file_key = d.pop("file_key")

        slug = d.pop("slug")

        node_ids = cast(list[str], d.pop("node_ids", UNSET))

        pages = cast(list[str], d.pop("pages", UNSET))

        title = d.pop("title", UNSET)

        figma_import_in = cls(
            file_key=file_key,
            slug=slug,
            node_ids=node_ids,
            pages=pages,
            title=title,
        )

        return figma_import_in
