from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="ReportSaved")


@_attrs_define
class ReportSaved:
    """
    Attributes:
        date (str):
        id (str):
        replaced (bool):
        time (str):
        type_ (str):
    """

    date: str
    id: str
    replaced: bool
    time: str
    type_: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        date = self.date

        id = self.id

        replaced = self.replaced

        time = self.time

        type_ = self.type_

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "date": date,
                "id": id,
                "replaced": replaced,
                "time": time,
                "type": type_,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        date = d.pop("date")

        id = d.pop("id")

        replaced = d.pop("replaced")

        time = d.pop("time")

        type_ = d.pop("type")

        report_saved = cls(
            date=date,
            id=id,
            replaced=replaced,
            time=time,
            type_=type_,
        )

        report_saved.additional_properties = d
        return report_saved

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
