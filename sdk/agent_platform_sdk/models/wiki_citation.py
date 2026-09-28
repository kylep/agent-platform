from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="WikiCitation")


@_attrs_define
class WikiCitation:
    """One room message that pointed at this page with a `[[slug]]`. Only ever
    from a room the reader may see: the page is the platform's, the conversation
    about it is still Relay's.

        Attributes:
            author (str):
            channel_id (str):
            created_at (None | str):
            message_id (str):
    """

    author: str
    channel_id: str
    created_at: None | str
    message_id: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        author = self.author

        channel_id = self.channel_id

        created_at: None | str
        created_at = self.created_at

        message_id = self.message_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "author": author,
                "channel_id": channel_id,
                "created_at": created_at,
                "message_id": message_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        author = d.pop("author")

        channel_id = d.pop("channel_id")

        def _parse_created_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        created_at = _parse_created_at(d.pop("created_at"))

        message_id = d.pop("message_id")

        wiki_citation = cls(
            author=author,
            channel_id=channel_id,
            created_at=created_at,
            message_id=message_id,
        )

        wiki_citation.additional_properties = d
        return wiki_citation

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
