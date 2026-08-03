from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.report_detail_meta import ReportDetailMeta


T = TypeVar("T", bound="ReportDetail")


@_attrs_define
class ReportDetail:
    """
    Attributes:
        created_at (None | str):
        date (str):
        html (str):
        id (str):
        meta (ReportDetailMeta):
        run_id (None | str):
        time (str):
        title (str):
        type_ (str):
        updated_at (None | str):
    """

    created_at: None | str
    date: str
    html: str
    id: str
    meta: ReportDetailMeta
    run_id: None | str
    time: str
    title: str
    type_: str
    updated_at: None | str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        created_at: None | str
        created_at = self.created_at

        date = self.date

        html = self.html

        id = self.id

        meta = self.meta.to_dict()

        run_id: None | str
        run_id = self.run_id

        time = self.time

        title = self.title

        type_ = self.type_

        updated_at: None | str
        updated_at = self.updated_at

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "date": date,
                "html": html,
                "id": id,
                "meta": meta,
                "run_id": run_id,
                "time": time,
                "title": title,
                "type": type_,
                "updated_at": updated_at,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.report_detail_meta import ReportDetailMeta

        d = dict(src_dict)

        def _parse_created_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        created_at = _parse_created_at(d.pop("created_at"))

        date = d.pop("date")

        html = d.pop("html")

        id = d.pop("id")

        meta = ReportDetailMeta.from_dict(d.pop("meta"))

        def _parse_run_id(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        run_id = _parse_run_id(d.pop("run_id"))

        time = d.pop("time")

        title = d.pop("title")

        type_ = d.pop("type")

        def _parse_updated_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        updated_at = _parse_updated_at(d.pop("updated_at"))

        report_detail = cls(
            created_at=created_at,
            date=date,
            html=html,
            id=id,
            meta=meta,
            run_id=run_id,
            time=time,
            title=title,
            type_=type_,
            updated_at=updated_at,
        )

        report_detail.additional_properties = d
        return report_detail

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
