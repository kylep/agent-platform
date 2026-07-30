from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

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
    """

    author: str
    branch: str
    created_at: str
    number: int
    title: str
    url: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        author = self.author

        branch = self.branch

        created_at = self.created_at

        number = self.number

        title = self.title

        url = self.url

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

        pull_request = cls(
            author=author,
            branch=branch,
            created_at=created_at,
            number=number,
            title=title,
            url=url,
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
