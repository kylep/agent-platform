from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="UiDocSummary")


@_attrs_define
class UiDocSummary:
    """
    Attributes:
        bytes_ (int):
        doc_sha256 (str):
        expanded_count (int):
        id (str):
        node_count (int):
        owner (str):
        slug (str):
        title (str):
        version (int):
        created_at (None | str | Unset):
        deleted_at (None | str | Unset):
        description (str | Unset):  Default: ''.
        updated_at (None | str | Unset):
    """

    bytes_: int
    doc_sha256: str
    expanded_count: int
    id: str
    node_count: int
    owner: str
    slug: str
    title: str
    version: int
    created_at: None | str | Unset = UNSET
    deleted_at: None | str | Unset = UNSET
    description: str | Unset = ""
    updated_at: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        bytes_ = self.bytes_

        doc_sha256 = self.doc_sha256

        expanded_count = self.expanded_count

        id = self.id

        node_count = self.node_count

        owner = self.owner

        slug = self.slug

        title = self.title

        version = self.version

        created_at: None | str | Unset
        if isinstance(self.created_at, Unset):
            created_at = UNSET
        else:
            created_at = self.created_at

        deleted_at: None | str | Unset
        if isinstance(self.deleted_at, Unset):
            deleted_at = UNSET
        else:
            deleted_at = self.deleted_at

        description = self.description

        updated_at: None | str | Unset
        if isinstance(self.updated_at, Unset):
            updated_at = UNSET
        else:
            updated_at = self.updated_at

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "bytes": bytes_,
                "doc_sha256": doc_sha256,
                "expanded_count": expanded_count,
                "id": id,
                "node_count": node_count,
                "owner": owner,
                "slug": slug,
                "title": title,
                "version": version,
            }
        )
        if created_at is not UNSET:
            field_dict["created_at"] = created_at
        if deleted_at is not UNSET:
            field_dict["deleted_at"] = deleted_at
        if description is not UNSET:
            field_dict["description"] = description
        if updated_at is not UNSET:
            field_dict["updated_at"] = updated_at

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        bytes_ = d.pop("bytes")

        doc_sha256 = d.pop("doc_sha256")

        expanded_count = d.pop("expanded_count")

        id = d.pop("id")

        node_count = d.pop("node_count")

        owner = d.pop("owner")

        slug = d.pop("slug")

        title = d.pop("title")

        version = d.pop("version")

        def _parse_created_at(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        created_at = _parse_created_at(d.pop("created_at", UNSET))

        def _parse_deleted_at(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        deleted_at = _parse_deleted_at(d.pop("deleted_at", UNSET))

        description = d.pop("description", UNSET)

        def _parse_updated_at(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        updated_at = _parse_updated_at(d.pop("updated_at", UNSET))

        ui_doc_summary = cls(
            bytes_=bytes_,
            doc_sha256=doc_sha256,
            expanded_count=expanded_count,
            id=id,
            node_count=node_count,
            owner=owner,
            slug=slug,
            title=title,
            version=version,
            created_at=created_at,
            deleted_at=deleted_at,
            description=description,
            updated_at=updated_at,
        )

        ui_doc_summary.additional_properties = d
        return ui_doc_summary

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
