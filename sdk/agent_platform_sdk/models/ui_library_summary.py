from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.ui_library_summary_counts import UiLibrarySummaryCounts


T = TypeVar("T", bound="UiLibrarySummary")


@_attrs_define
class UiLibrarySummary:
    """
    Attributes:
        counts (UiLibrarySummaryCounts):
        library_id (str):
        namespace (str):
        source_id (str):
        source_slug (str):
        source_version (int):
    """

    counts: UiLibrarySummaryCounts
    library_id: str
    namespace: str
    source_id: str
    source_slug: str
    source_version: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        counts = self.counts.to_dict()

        library_id = self.library_id

        namespace = self.namespace

        source_id = self.source_id

        source_slug = self.source_slug

        source_version = self.source_version

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "counts": counts,
                "library_id": library_id,
                "namespace": namespace,
                "source_id": source_id,
                "source_slug": source_slug,
                "source_version": source_version,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.ui_library_summary_counts import UiLibrarySummaryCounts

        d = dict(src_dict)
        counts = UiLibrarySummaryCounts.from_dict(d.pop("counts"))

        library_id = d.pop("library_id")

        namespace = d.pop("namespace")

        source_id = d.pop("source_id")

        source_slug = d.pop("source_slug")

        source_version = d.pop("source_version")

        ui_library_summary = cls(
            counts=counts,
            library_id=library_id,
            namespace=namespace,
            source_id=source_id,
            source_slug=source_slug,
            source_version=source_version,
        )

        ui_library_summary.additional_properties = d
        return ui_library_summary

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
