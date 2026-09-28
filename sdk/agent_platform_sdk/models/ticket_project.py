from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="TicketProject")


@_attrs_define
class TicketProject:
    """A Relay channel that has a prefix, with the two counts a project picker
    shows.

        Attributes:
            id (str):
            in_progress (int):
            name (None | str):
            open_ (int):
            prefix (str):
            title (None | str):
    """

    id: str
    in_progress: int
    name: None | str
    open_: int
    prefix: str
    title: None | str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = self.id

        in_progress = self.in_progress

        name: None | str
        name = self.name

        open_ = self.open_

        prefix = self.prefix

        title: None | str
        title = self.title

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "in_progress": in_progress,
                "name": name,
                "open": open_,
                "prefix": prefix,
                "title": title,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        id = d.pop("id")

        in_progress = d.pop("in_progress")

        def _parse_name(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        name = _parse_name(d.pop("name"))

        open_ = d.pop("open")

        prefix = d.pop("prefix")

        def _parse_title(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        title = _parse_title(d.pop("title"))

        ticket_project = cls(
            id=id,
            in_progress=in_progress,
            name=name,
            open_=open_,
            prefix=prefix,
            title=title,
        )

        ticket_project.additional_properties = d
        return ticket_project

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
