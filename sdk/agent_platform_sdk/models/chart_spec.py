from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.chart_series import ChartSeries


T = TypeVar("T", bound="ChartSpec")


@_attrs_define
class ChartSpec:
    """
    Attributes:
        kind (str):
        series (list[ChartSeries]):
        height (int | Unset):  Default: 240.
        labels (list[str] | Unset):
        title (str | Unset):  Default: ''.
        width (int | Unset):  Default: 640.
    """

    kind: str
    series: list[ChartSeries]
    height: int | Unset = 240
    labels: list[str] | Unset = UNSET
    title: str | Unset = ""
    width: int | Unset = 640
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        kind = self.kind

        series = []
        for series_item_data in self.series:
            series_item = series_item_data.to_dict()
            series.append(series_item)

        height = self.height

        labels: list[str] | Unset = UNSET
        if not isinstance(self.labels, Unset):
            labels = self.labels

        title = self.title

        width = self.width

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "kind": kind,
                "series": series,
            }
        )
        if height is not UNSET:
            field_dict["height"] = height
        if labels is not UNSET:
            field_dict["labels"] = labels
        if title is not UNSET:
            field_dict["title"] = title
        if width is not UNSET:
            field_dict["width"] = width

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.chart_series import ChartSeries

        d = dict(src_dict)
        kind = d.pop("kind")

        series = []
        _series = d.pop("series")
        for series_item_data in _series:
            series_item = ChartSeries.from_dict(series_item_data)

            series.append(series_item)

        height = d.pop("height", UNSET)

        labels = cast(list[str], d.pop("labels", UNSET))

        title = d.pop("title", UNSET)

        width = d.pop("width", UNSET)

        chart_spec = cls(
            kind=kind,
            series=series,
            height=height,
            labels=labels,
            title=title,
            width=width,
        )

        chart_spec.additional_properties = d
        return chart_spec

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
