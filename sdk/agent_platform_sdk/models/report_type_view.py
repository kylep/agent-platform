from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="ReportTypeView")


@_attrs_define
class ReportTypeView:
    """
    Attributes:
        cadence (str):
        count (int):
        description (str):
        error (None | str):
        generator (str):
        icon (str):
        latest_date (None | str):
        name (str):
        retention_days (int):
    """

    cadence: str
    count: int
    description: str
    error: None | str
    generator: str
    icon: str
    latest_date: None | str
    name: str
    retention_days: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        cadence = self.cadence

        count = self.count

        description = self.description

        error: None | str
        error = self.error

        generator = self.generator

        icon = self.icon

        latest_date: None | str
        latest_date = self.latest_date

        name = self.name

        retention_days = self.retention_days

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "cadence": cadence,
                "count": count,
                "description": description,
                "error": error,
                "generator": generator,
                "icon": icon,
                "latest_date": latest_date,
                "name": name,
                "retention_days": retention_days,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        cadence = d.pop("cadence")

        count = d.pop("count")

        description = d.pop("description")

        def _parse_error(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        error = _parse_error(d.pop("error"))

        generator = d.pop("generator")

        icon = d.pop("icon")

        def _parse_latest_date(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        latest_date = _parse_latest_date(d.pop("latest_date"))

        name = d.pop("name")

        retention_days = d.pop("retention_days")

        report_type_view = cls(
            cadence=cadence,
            count=count,
            description=description,
            error=error,
            generator=generator,
            icon=icon,
            latest_date=latest_date,
            name=name,
            retention_days=retention_days,
        )

        report_type_view.additional_properties = d
        return report_type_view

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
