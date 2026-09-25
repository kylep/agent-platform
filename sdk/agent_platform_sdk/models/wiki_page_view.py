from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.relay_face import RelayFace


T = TypeVar("T", bound="WikiPageView")


@_attrs_define
class WikiPageView:
    """
    Attributes:
        archived_at (None | str):
        body (str):
        created_at (None | str):
        created_by (str):
        id (str):
        slug (str):
        source_memory_id (None | str):
        summary (str):
        tags (list[str]):
        title (str):
        updated_at (None | str):
        updated_by (str):
        version (int):
        updated_by_face (None | RelayFace | Unset):
    """

    archived_at: None | str
    body: str
    created_at: None | str
    created_by: str
    id: str
    slug: str
    source_memory_id: None | str
    summary: str
    tags: list[str]
    title: str
    updated_at: None | str
    updated_by: str
    version: int
    updated_by_face: None | RelayFace | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.relay_face import RelayFace

        archived_at: None | str
        archived_at = self.archived_at

        body = self.body

        created_at: None | str
        created_at = self.created_at

        created_by = self.created_by

        id = self.id

        slug = self.slug

        source_memory_id: None | str
        source_memory_id = self.source_memory_id

        summary = self.summary

        tags = self.tags

        title = self.title

        updated_at: None | str
        updated_at = self.updated_at

        updated_by = self.updated_by

        version = self.version

        updated_by_face: dict[str, Any] | None | Unset
        if isinstance(self.updated_by_face, Unset):
            updated_by_face = UNSET
        elif isinstance(self.updated_by_face, RelayFace):
            updated_by_face = self.updated_by_face.to_dict()
        else:
            updated_by_face = self.updated_by_face

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "archived_at": archived_at,
                "body": body,
                "created_at": created_at,
                "created_by": created_by,
                "id": id,
                "slug": slug,
                "source_memory_id": source_memory_id,
                "summary": summary,
                "tags": tags,
                "title": title,
                "updated_at": updated_at,
                "updated_by": updated_by,
                "version": version,
            }
        )
        if updated_by_face is not UNSET:
            field_dict["updated_by_face"] = updated_by_face

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.relay_face import RelayFace

        d = dict(src_dict)

        def _parse_archived_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        archived_at = _parse_archived_at(d.pop("archived_at"))

        body = d.pop("body")

        def _parse_created_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        created_at = _parse_created_at(d.pop("created_at"))

        created_by = d.pop("created_by")

        id = d.pop("id")

        slug = d.pop("slug")

        def _parse_source_memory_id(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        source_memory_id = _parse_source_memory_id(d.pop("source_memory_id"))

        summary = d.pop("summary")

        tags = cast(list[str], d.pop("tags"))

        title = d.pop("title")

        def _parse_updated_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        updated_at = _parse_updated_at(d.pop("updated_at"))

        updated_by = d.pop("updated_by")

        version = d.pop("version")

        def _parse_updated_by_face(data: object) -> None | RelayFace | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                updated_by_face_type_0 = RelayFace.from_dict(data)

                return updated_by_face_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | RelayFace | Unset, data)

        updated_by_face = _parse_updated_by_face(d.pop("updated_by_face", UNSET))

        wiki_page_view = cls(
            archived_at=archived_at,
            body=body,
            created_at=created_at,
            created_by=created_by,
            id=id,
            slug=slug,
            source_memory_id=source_memory_id,
            summary=summary,
            tags=tags,
            title=title,
            updated_at=updated_at,
            updated_by=updated_by,
            version=version,
            updated_by_face=updated_by_face,
        )

        wiki_page_view.additional_properties = d
        return wiki_page_view

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
