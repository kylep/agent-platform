from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.ui_doc_diff_out_chunks_item import UiDocDiffOutChunksItem


T = TypeVar("T", bound="UiDocDiffOut")


@_attrs_define
class UiDocDiffOut:
    """
    Attributes:
        chunks (list[UiDocDiffOutChunksItem]):
        from_version (int):
        slug (str):
        text (str):
        to_version (int):
        version (int):
        truncated (bool | Unset):  Default: False.
    """

    chunks: list[UiDocDiffOutChunksItem]
    from_version: int
    slug: str
    text: str
    to_version: int
    version: int
    truncated: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        chunks = []
        for chunks_item_data in self.chunks:
            chunks_item = chunks_item_data.to_dict()
            chunks.append(chunks_item)

        from_version = self.from_version

        slug = self.slug

        text = self.text

        to_version = self.to_version

        version = self.version

        truncated = self.truncated

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "chunks": chunks,
                "from_version": from_version,
                "slug": slug,
                "text": text,
                "to_version": to_version,
                "version": version,
            }
        )
        if truncated is not UNSET:
            field_dict["truncated"] = truncated

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.ui_doc_diff_out_chunks_item import UiDocDiffOutChunksItem

        d = dict(src_dict)
        chunks = []
        _chunks = d.pop("chunks")
        for chunks_item_data in _chunks:
            chunks_item = UiDocDiffOutChunksItem.from_dict(chunks_item_data)

            chunks.append(chunks_item)

        from_version = d.pop("from_version")

        slug = d.pop("slug")

        text = d.pop("text")

        to_version = d.pop("to_version")

        version = d.pop("version")

        truncated = d.pop("truncated", UNSET)

        ui_doc_diff_out = cls(
            chunks=chunks,
            from_version=from_version,
            slug=slug,
            text=text,
            to_version=to_version,
            version=version,
            truncated=truncated,
        )

        ui_doc_diff_out.additional_properties = d
        return ui_doc_diff_out

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
