from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="PullRequest")


@_attrs_define
class PullRequest:
    """
    Attributes:
        author (str):
        branch (str):
        created_at (str):
        number (int):
        title (str):
        url (str):
        agent (None | str | Unset):
        auto_merge (bool | None | Unset):
        ticket_key (None | str | Unset):
    """

    author: str
    branch: str
    created_at: str
    number: int
    title: str
    url: str
    agent: None | str | Unset = UNSET
    auto_merge: bool | None | Unset = UNSET
    ticket_key: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        author = self.author

        branch = self.branch

        created_at = self.created_at

        number = self.number

        title = self.title

        url = self.url

        agent: None | str | Unset
        if isinstance(self.agent, Unset):
            agent = UNSET
        else:
            agent = self.agent

        auto_merge: bool | None | Unset
        if isinstance(self.auto_merge, Unset):
            auto_merge = UNSET
        else:
            auto_merge = self.auto_merge

        ticket_key: None | str | Unset
        if isinstance(self.ticket_key, Unset):
            ticket_key = UNSET
        else:
            ticket_key = self.ticket_key

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "author": author,
                "branch": branch,
                "created_at": created_at,
                "number": number,
                "title": title,
                "url": url,
            }
        )
        if agent is not UNSET:
            field_dict["agent"] = agent
        if auto_merge is not UNSET:
            field_dict["auto_merge"] = auto_merge
        if ticket_key is not UNSET:
            field_dict["ticket_key"] = ticket_key

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        author = d.pop("author")

        branch = d.pop("branch")

        created_at = d.pop("created_at")

        number = d.pop("number")

        title = d.pop("title")

        url = d.pop("url")

        def _parse_agent(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        agent = _parse_agent(d.pop("agent", UNSET))

        def _parse_auto_merge(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        auto_merge = _parse_auto_merge(d.pop("auto_merge", UNSET))

        def _parse_ticket_key(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        ticket_key = _parse_ticket_key(d.pop("ticket_key", UNSET))

        pull_request = cls(
            author=author,
            branch=branch,
            created_at=created_at,
            number=number,
            title=title,
            url=url,
            agent=agent,
            auto_merge=auto_merge,
            ticket_key=ticket_key,
        )

        pull_request.additional_properties = d
        return pull_request

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
