from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.artifact_view_meta import ArtifactViewMeta


T = TypeVar("T", bound="ArtifactView")


@_attrs_define
class ArtifactView:
    """
    Attributes:
        content_url (str):
        created_at (None | str):
        deleted_at (None | str):
        height (int | None):
        id (str):
        kind (str):
        meta (ArtifactViewMeta):
        mime (str):
        name (str):
        owner (str):
        run_id (None | str):
        sha256 (str):
        size (int):
        source (str):
        tags (list[str]):
        thumb_url (None | str):
        width (int | None):
    """

    content_url: str
    created_at: None | str
    deleted_at: None | str
    height: int | None
    id: str
    kind: str
    meta: ArtifactViewMeta
    mime: str
    name: str
    owner: str
    run_id: None | str
    sha256: str
    size: int
    source: str
    tags: list[str]
    thumb_url: None | str
    width: int | None
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        content_url = self.content_url

        created_at: None | str
        created_at = self.created_at

        deleted_at: None | str
        deleted_at = self.deleted_at

        height: int | None
        height = self.height

        id = self.id

        kind = self.kind

        meta = self.meta.to_dict()

        mime = self.mime

        name = self.name

        owner = self.owner

        run_id: None | str
        run_id = self.run_id

        sha256 = self.sha256

        size = self.size

        source = self.source

        tags = self.tags

        thumb_url: None | str
        thumb_url = self.thumb_url

        width: int | None
        width = self.width

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "content_url": content_url,
                "created_at": created_at,
                "deleted_at": deleted_at,
                "height": height,
                "id": id,
                "kind": kind,
                "meta": meta,
                "mime": mime,
                "name": name,
                "owner": owner,
                "run_id": run_id,
                "sha256": sha256,
                "size": size,
                "source": source,
                "tags": tags,
                "thumb_url": thumb_url,
                "width": width,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.artifact_view_meta import ArtifactViewMeta

        d = dict(src_dict)
        content_url = d.pop("content_url")

        def _parse_created_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        created_at = _parse_created_at(d.pop("created_at"))

        def _parse_deleted_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        deleted_at = _parse_deleted_at(d.pop("deleted_at"))

        def _parse_height(data: object) -> int | None:
            if data is None:
                return data
            return cast(int | None, data)

        height = _parse_height(d.pop("height"))

        id = d.pop("id")

        kind = d.pop("kind")

        meta = ArtifactViewMeta.from_dict(d.pop("meta"))

        mime = d.pop("mime")

        name = d.pop("name")

        owner = d.pop("owner")

        def _parse_run_id(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        run_id = _parse_run_id(d.pop("run_id"))

        sha256 = d.pop("sha256")

        size = d.pop("size")

        source = d.pop("source")

        tags = cast(list[str], d.pop("tags"))

        def _parse_thumb_url(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        thumb_url = _parse_thumb_url(d.pop("thumb_url"))

        def _parse_width(data: object) -> int | None:
            if data is None:
                return data
            return cast(int | None, data)

        width = _parse_width(d.pop("width"))

        artifact_view = cls(
            content_url=content_url,
            created_at=created_at,
            deleted_at=deleted_at,
            height=height,
            id=id,
            kind=kind,
            meta=meta,
            mime=mime,
            name=name,
            owner=owner,
            run_id=run_id,
            sha256=sha256,
            size=size,
            source=source,
            tags=tags,
            thumb_url=thumb_url,
            width=width,
        )

        artifact_view.additional_properties = d
        return artifact_view

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
