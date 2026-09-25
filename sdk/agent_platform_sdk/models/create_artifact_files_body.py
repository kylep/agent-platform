from __future__ import annotations

from collections.abc import Mapping
from io import BytesIO
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from .. import types
from ..types import UNSET, File, Unset

T = TypeVar("T", bound="CreateArtifactFilesBody")


@_attrs_define
class CreateArtifactFilesBody:
    """
    Attributes:
        file (File):
        meta (str | Unset): JSON object
        name (str | Unset):
        tags (str | Unset): JSON list or comma-separated
    """

    file: File
    meta: str | Unset = UNSET
    name: str | Unset = UNSET
    tags: str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        file = self.file.to_tuple()

        meta = self.meta

        name = self.name

        tags = self.tags

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "file": file,
            }
        )
        if meta is not UNSET:
            field_dict["meta"] = meta
        if name is not UNSET:
            field_dict["name"] = name
        if tags is not UNSET:
            field_dict["tags"] = tags

        return field_dict

    def to_multipart(self) -> types.RequestFiles:
        files: types.RequestFiles = []

        files.append(("file", self.file.to_tuple()))

        if not isinstance(self.meta, Unset):
            files.append(("meta", (None, str(self.meta).encode(), "text/plain")))

        if not isinstance(self.name, Unset):
            files.append(("name", (None, str(self.name).encode(), "text/plain")))

        if not isinstance(self.tags, Unset):
            files.append(("tags", (None, str(self.tags).encode(), "text/plain")))

        for prop_name, prop in self.additional_properties.items():
            files.append((prop_name, (None, str(prop).encode(), "text/plain")))

        return files

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        file = File(payload=BytesIO(d.pop("file")))

        meta = d.pop("meta", UNSET)

        name = d.pop("name", UNSET)

        tags = d.pop("tags", UNSET)

        create_artifact_files_body = cls(
            file=file,
            meta=meta,
            name=name,
            tags=tags,
        )

        create_artifact_files_body.additional_properties = d
        return create_artifact_files_body

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
