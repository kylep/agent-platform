from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.create_artifact_json_artifact_in_source import (
    CreateArtifactJsonArtifactInSource,
)
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.create_artifact_json_artifact_in_meta_type_0 import (
        CreateArtifactJsonArtifactInMetaType0,
    )


T = TypeVar("T", bound="CreateArtifactJsonArtifactIn")


@_attrs_define
class CreateArtifactJsonArtifactIn:
    """The JSON create: text, or bytes as base64, exactly one of them. `mime`
    is a CLAIM the store honours only for a few text types over bytes that
    decode. `source` may not say `generated` — that is the generate route's
    word for something it paid for.

        Attributes:
            content_b64 (None | str | Unset):
            meta (CreateArtifactJsonArtifactInMetaType0 | None | Unset):
            mime (None | str | Unset):
            name (str | Unset):  Default: ''.
            source (CreateArtifactJsonArtifactInSource | Unset):  Default: CreateArtifactJsonArtifactInSource.UPLOAD.
            tags (list[str] | None | Unset):
            text (None | str | Unset):
    """

    content_b64: None | str | Unset = UNSET
    meta: CreateArtifactJsonArtifactInMetaType0 | None | Unset = UNSET
    mime: None | str | Unset = UNSET
    name: str | Unset = ""
    source: CreateArtifactJsonArtifactInSource | Unset = (
        CreateArtifactJsonArtifactInSource.UPLOAD
    )
    tags: list[str] | None | Unset = UNSET
    text: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.create_artifact_json_artifact_in_meta_type_0 import (
            CreateArtifactJsonArtifactInMetaType0,
        )

        content_b64: None | str | Unset
        if isinstance(self.content_b64, Unset):
            content_b64 = UNSET
        else:
            content_b64 = self.content_b64

        meta: dict[str, Any] | None | Unset
        if isinstance(self.meta, Unset):
            meta = UNSET
        elif isinstance(self.meta, CreateArtifactJsonArtifactInMetaType0):
            meta = self.meta.to_dict()
        else:
            meta = self.meta

        mime: None | str | Unset
        if isinstance(self.mime, Unset):
            mime = UNSET
        else:
            mime = self.mime

        name = self.name

        source: str | Unset = UNSET
        if not isinstance(self.source, Unset):
            source = self.source.value

        tags: list[str] | None | Unset
        if isinstance(self.tags, Unset):
            tags = UNSET
        elif isinstance(self.tags, list):
            tags = self.tags

        else:
            tags = self.tags

        text: None | str | Unset
        if isinstance(self.text, Unset):
            text = UNSET
        else:
            text = self.text

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if content_b64 is not UNSET:
            field_dict["content_b64"] = content_b64
        if meta is not UNSET:
            field_dict["meta"] = meta
        if mime is not UNSET:
            field_dict["mime"] = mime
        if name is not UNSET:
            field_dict["name"] = name
        if source is not UNSET:
            field_dict["source"] = source
        if tags is not UNSET:
            field_dict["tags"] = tags
        if text is not UNSET:
            field_dict["text"] = text

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.create_artifact_json_artifact_in_meta_type_0 import (
            CreateArtifactJsonArtifactInMetaType0,
        )

        d = dict(src_dict)

        def _parse_content_b64(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        content_b64 = _parse_content_b64(d.pop("content_b64", UNSET))

        def _parse_meta(
            data: object,
        ) -> CreateArtifactJsonArtifactInMetaType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                meta_type_0 = CreateArtifactJsonArtifactInMetaType0.from_dict(data)

                return meta_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(CreateArtifactJsonArtifactInMetaType0 | None | Unset, data)

        meta = _parse_meta(d.pop("meta", UNSET))

        def _parse_mime(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        mime = _parse_mime(d.pop("mime", UNSET))

        name = d.pop("name", UNSET)

        _source = d.pop("source", UNSET)
        source: CreateArtifactJsonArtifactInSource | Unset
        if isinstance(_source, Unset):
            source = UNSET
        else:
            source = CreateArtifactJsonArtifactInSource(_source)

        def _parse_tags(data: object) -> list[str] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                tags_type_0 = cast(list[str], data)

                return tags_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[str] | None | Unset, data)

        tags = _parse_tags(d.pop("tags", UNSET))

        def _parse_text(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        text = _parse_text(d.pop("text", UNSET))

        create_artifact_json_artifact_in = cls(
            content_b64=content_b64,
            meta=meta,
            mime=mime,
            name=name,
            source=source,
            tags=tags,
            text=text,
        )

        create_artifact_json_artifact_in.additional_properties = d
        return create_artifact_json_artifact_in

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
