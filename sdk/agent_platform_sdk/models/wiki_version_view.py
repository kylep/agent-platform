from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.relay_face import RelayFace


T = TypeVar("T", bound="WikiVersionView")


@_attrs_define
class WikiVersionView:
    """
    Attributes:
        author (str):
        body (str):
        created_at (None | str):
        id (str):
        page_id (str):
        reason (str):
        run_id (None | str):
        title (str):
        version (int):
        author_face (None | RelayFace | Unset):
    """

    author: str
    body: str
    created_at: None | str
    id: str
    page_id: str
    reason: str
    run_id: None | str
    title: str
    version: int
    author_face: None | RelayFace | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.relay_face import RelayFace

        author = self.author

        body = self.body

        created_at: None | str
        created_at = self.created_at

        id = self.id

        page_id = self.page_id

        reason = self.reason

        run_id: None | str
        run_id = self.run_id

        title = self.title

        version = self.version

        author_face: dict[str, Any] | None | Unset
        if isinstance(self.author_face, Unset):
            author_face = UNSET
        elif isinstance(self.author_face, RelayFace):
            author_face = self.author_face.to_dict()
        else:
            author_face = self.author_face

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "author": author,
                "body": body,
                "created_at": created_at,
                "id": id,
                "page_id": page_id,
                "reason": reason,
                "run_id": run_id,
                "title": title,
                "version": version,
            }
        )
        if author_face is not UNSET:
            field_dict["author_face"] = author_face

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.relay_face import RelayFace

        d = dict(src_dict)
        author = d.pop("author")

        body = d.pop("body")

        def _parse_created_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        created_at = _parse_created_at(d.pop("created_at"))

        id = d.pop("id")

        page_id = d.pop("page_id")

        reason = d.pop("reason")

        def _parse_run_id(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        run_id = _parse_run_id(d.pop("run_id"))

        title = d.pop("title")

        version = d.pop("version")

        def _parse_author_face(data: object) -> None | RelayFace | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                author_face_type_0 = RelayFace.from_dict(data)

                return author_face_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | RelayFace | Unset, data)

        author_face = _parse_author_face(d.pop("author_face", UNSET))

        wiki_version_view = cls(
            author=author,
            body=body,
            created_at=created_at,
            id=id,
            page_id=page_id,
            reason=reason,
            run_id=run_id,
            title=title,
            version=version,
            author_face=author_face,
        )

        wiki_version_view.additional_properties = d
        return wiki_version_view

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
