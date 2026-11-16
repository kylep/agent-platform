from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.figma_import_out_state import FigmaImportOutState
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.figma_import_out_pages import FigmaImportOutPages
    from ..models.figma_import_out_report import FigmaImportOutReport


T = TypeVar("T", bound="FigmaImportOut")


@_attrs_define
class FigmaImportOut:
    """
    Attributes:
        created_at (datetime.datetime):
        file_key (str):
        id (str):
        pages (FigmaImportOutPages):
        report (FigmaImportOutReport):
        slug (str):
        state (FigmaImportOutState):
        updated_at (datetime.datetime):
        error (None | str | Unset):
    """

    created_at: datetime.datetime
    file_key: str
    id: str
    pages: FigmaImportOutPages
    report: FigmaImportOutReport
    slug: str
    state: FigmaImportOutState
    updated_at: datetime.datetime
    error: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        created_at = self.created_at.isoformat()

        file_key = self.file_key

        id = self.id

        pages = self.pages.to_dict()

        report = self.report.to_dict()

        slug = self.slug

        state = self.state.value

        updated_at = self.updated_at.isoformat()

        error: None | str | Unset
        if isinstance(self.error, Unset):
            error = UNSET
        else:
            error = self.error

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "file_key": file_key,
                "id": id,
                "pages": pages,
                "report": report,
                "slug": slug,
                "state": state,
                "updated_at": updated_at,
            }
        )
        if error is not UNSET:
            field_dict["error"] = error

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.figma_import_out_pages import FigmaImportOutPages
        from ..models.figma_import_out_report import FigmaImportOutReport

        d = dict(src_dict)
        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        file_key = d.pop("file_key")

        id = d.pop("id")

        pages = FigmaImportOutPages.from_dict(d.pop("pages"))

        report = FigmaImportOutReport.from_dict(d.pop("report"))

        slug = d.pop("slug")

        state = FigmaImportOutState(d.pop("state"))

        updated_at = datetime.datetime.fromisoformat(d.pop("updated_at"))

        def _parse_error(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        error = _parse_error(d.pop("error", UNSET))

        figma_import_out = cls(
            created_at=created_at,
            file_key=file_key,
            id=id,
            pages=pages,
            report=report,
            slug=slug,
            state=state,
            updated_at=updated_at,
            error=error,
        )

        figma_import_out.additional_properties = d
        return figma_import_out

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
