from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.report_in_meta import ReportInMeta


T = TypeVar("T", bound="ReportIn")


@_attrs_define
class ReportIn:
    """
    Attributes:
        date (str):
        html (str):
        type_ (str):
        meta (ReportInMeta | Unset):
        time (str | Unset):  Default: ''.
        title (str | Unset):  Default: ''.
    """

    date: str
    html: str
    type_: str
    meta: ReportInMeta | Unset = UNSET
    time: str | Unset = ""
    title: str | Unset = ""
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        date = self.date

        html = self.html

        type_ = self.type_

        meta: dict[str, Any] | Unset = UNSET
        if not isinstance(self.meta, Unset):
            meta = self.meta.to_dict()

        time = self.time

        title = self.title

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "date": date,
                "html": html,
                "type": type_,
            }
        )
        if meta is not UNSET:
            field_dict["meta"] = meta
        if time is not UNSET:
            field_dict["time"] = time
        if title is not UNSET:
            field_dict["title"] = title

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.report_in_meta import ReportInMeta

        d = dict(src_dict)
        date = d.pop("date")

        html = d.pop("html")

        type_ = d.pop("type")

        _meta = d.pop("meta", UNSET)
        meta: ReportInMeta | Unset
        if isinstance(_meta, Unset):
            meta = UNSET
        else:
            meta = ReportInMeta.from_dict(_meta)

        time = d.pop("time", UNSET)

        title = d.pop("title", UNSET)

        report_in = cls(
            date=date,
            html=html,
            type_=type_,
            meta=meta,
            time=time,
            title=title,
        )

        report_in.additional_properties = d
        return report_in

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
