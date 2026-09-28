from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="WikiWriteIn")


@_attrs_define
class WikiWriteIn:
    """A full replacement of a page's body. `base_version` is REQUIRED and
    unset is a 422: a write that never says what it read is a writer claiming
    the page has not moved without having looked, and the loser of two of those
    silently erases the winner.

        Attributes:
            base_version (int):
            body (str):
            reason (str | Unset):  Default: ''.
            tags (list[str] | None | Unset):
            title (None | str | Unset):
    """

    base_version: int
    body: str
    reason: str | Unset = ""
    tags: list[str] | None | Unset = UNSET
    title: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        base_version = self.base_version

        body = self.body

        reason = self.reason

        tags: list[str] | None | Unset
        if isinstance(self.tags, Unset):
            tags = UNSET
        elif isinstance(self.tags, list):
            tags = self.tags

        else:
            tags = self.tags

        title: None | str | Unset
        if isinstance(self.title, Unset):
            title = UNSET
        else:
            title = self.title

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "base_version": base_version,
                "body": body,
            }
        )
        if reason is not UNSET:
            field_dict["reason"] = reason
        if tags is not UNSET:
            field_dict["tags"] = tags
        if title is not UNSET:
            field_dict["title"] = title

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        base_version = d.pop("base_version")

        body = d.pop("body")

        reason = d.pop("reason", UNSET)

        def _parse_tags(data: object) -> list[str] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                tags_type_0 = cast(list[str], data)

                return tags_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[str] | None | Unset, data)

        tags = _parse_tags(d.pop("tags", UNSET))

        def _parse_title(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        title = _parse_title(d.pop("title", UNSET))

        wiki_write_in = cls(
            base_version=base_version,
            body=body,
            reason=reason,
            tags=tags,
            title=title,
        )

        return wiki_write_in
