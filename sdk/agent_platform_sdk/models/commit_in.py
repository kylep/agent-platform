from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="CommitIn")


@_attrs_define
class CommitIn:
    """
    Attributes:
        destination (str):
        summary (str):
        follow_up (str | Unset):  Default: ''.
        meaning (str | Unset):  Default: ''.
        source_note (str | Unset):  Default: ''.
    """

    destination: str
    summary: str
    follow_up: str | Unset = ""
    meaning: str | Unset = ""
    source_note: str | Unset = ""
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        destination = self.destination

        summary = self.summary

        follow_up = self.follow_up

        meaning = self.meaning

        source_note = self.source_note

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "destination": destination,
                "summary": summary,
            }
        )
        if follow_up is not UNSET:
            field_dict["follow_up"] = follow_up
        if meaning is not UNSET:
            field_dict["meaning"] = meaning
        if source_note is not UNSET:
            field_dict["source_note"] = source_note

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        destination = d.pop("destination")

        summary = d.pop("summary")

        follow_up = d.pop("follow_up", UNSET)

        meaning = d.pop("meaning", UNSET)

        source_note = d.pop("source_note", UNSET)

        commit_in = cls(
            destination=destination,
            summary=summary,
            follow_up=follow_up,
            meaning=meaning,
            source_note=source_note,
        )

        commit_in.additional_properties = d
        return commit_in

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
