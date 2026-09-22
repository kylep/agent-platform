from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="WikiPromoteIn")


@_attrs_define
class WikiPromoteIn:
    """The memory to harden into a page, named exactly one way.

    `key` exists because the caller that most wants to promote cannot use an
    id: an agent holds the key it remembered under, and its participant token
    is refused by `/api/memories` (a `READ_ROLES` door the wiki's is not), so
    trading a key for an id over HTTP is not a trade it can make. The key is
    therefore resolved server-side, in the caller's own namespace — which is
    also why a human, who has no namespace of their own, must say `agent`.

        Attributes:
            agent (None | str | Unset):
            key (None | str | Unset):
            memory_id (None | str | Unset):
            slug (None | str | Unset):
            title (None | str | Unset):
    """

    agent: None | str | Unset = UNSET
    key: None | str | Unset = UNSET
    memory_id: None | str | Unset = UNSET
    slug: None | str | Unset = UNSET
    title: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        agent: None | str | Unset
        if isinstance(self.agent, Unset):
            agent = UNSET
        else:
            agent = self.agent

        key: None | str | Unset
        if isinstance(self.key, Unset):
            key = UNSET
        else:
            key = self.key

        memory_id: None | str | Unset
        if isinstance(self.memory_id, Unset):
            memory_id = UNSET
        else:
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

        field_dict.update({})
        if agent is not UNSET:
            field_dict["agent"] = agent
        if key is not UNSET:
            field_dict["key"] = key
        if memory_id is not UNSET:
            field_dict["memory_id"] = memory_id
        if slug is not UNSET:
            field_dict["slug"] = slug
        if title is not UNSET:
            field_dict["title"] = title

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_agent(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        agent = _parse_agent(d.pop("agent", UNSET))

        def _parse_key(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        key = _parse_key(d.pop("key", UNSET))

        def _parse_memory_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        memory_id = _parse_memory_id(d.pop("memory_id", UNSET))

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
            agent=agent,
            key=key,
            memory_id=memory_id,
            slug=slug,
            title=title,
        )

        return wiki_promote_in
