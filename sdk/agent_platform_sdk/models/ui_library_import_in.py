from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.ui_library_spec import UiLibrarySpec


T = TypeVar("T", bound="UiLibraryImportIn")


@_attrs_define
class UiLibraryImportIn:
    """
    Attributes:
        expected_version (int):
        library (UiLibrarySpec):
        request_id (str):
    """

    expected_version: int
    library: UiLibrarySpec
    request_id: str

    def to_dict(self) -> dict[str, Any]:
        expected_version = self.expected_version

        library = self.library.to_dict()

        request_id = self.request_id

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "expected_version": expected_version,
                "library": library,
                "request_id": request_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.ui_library_spec import UiLibrarySpec

        d = dict(src_dict)
        expected_version = d.pop("expected_version")

        library = UiLibrarySpec.from_dict(d.pop("library"))

        request_id = d.pop("request_id")

        ui_library_import_in = cls(
            expected_version=expected_version,
            library=library,
            request_id=request_id,
        )

        return ui_library_import_in
