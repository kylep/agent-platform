from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="UiMotionExportOut")


@_attrs_define
class UiMotionExportOut:
    """
    Attributes:
        artifact_id (str):
        document_id (str):
        duration_ms (int):
        frames (int):
        height (int):
        mime (str):
        name (str):
        size (int):
        slug (str):
        url (str):
        version (int):
        width (int):
        ok (bool | Unset):  Default: True.
        replayed (bool | Unset):  Default: False.
        warnings (list[str] | Unset):
    """

    artifact_id: str
    document_id: str
    duration_ms: int
    frames: int
    height: int
    mime: str
    name: str
    size: int
    slug: str
    url: str
    version: int
    width: int
    ok: bool | Unset = True
    replayed: bool | Unset = False
    warnings: list[str] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        artifact_id = self.artifact_id

        document_id = self.document_id

        duration_ms = self.duration_ms

        frames = self.frames

        height = self.height

        mime = self.mime

        name = self.name

        size = self.size

        slug = self.slug

        url = self.url

        version = self.version

        width = self.width

        ok = self.ok

        replayed = self.replayed

        warnings: list[str] | Unset = UNSET
        if not isinstance(self.warnings, Unset):
            warnings = self.warnings

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "artifact_id": artifact_id,
                "document_id": document_id,
                "duration_ms": duration_ms,
                "frames": frames,
                "height": height,
                "mime": mime,
                "name": name,
                "size": size,
                "slug": slug,
                "url": url,
                "version": version,
                "width": width,
            }
        )
        if ok is not UNSET:
            field_dict["ok"] = ok
        if replayed is not UNSET:
            field_dict["replayed"] = replayed
        if warnings is not UNSET:
            field_dict["warnings"] = warnings

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        artifact_id = d.pop("artifact_id")

        document_id = d.pop("document_id")

        duration_ms = d.pop("duration_ms")

        frames = d.pop("frames")

        height = d.pop("height")

        mime = d.pop("mime")

        name = d.pop("name")

        size = d.pop("size")

        slug = d.pop("slug")

        url = d.pop("url")

        version = d.pop("version")

        width = d.pop("width")

        ok = d.pop("ok", UNSET)

        replayed = d.pop("replayed", UNSET)

        warnings = cast(list[str], d.pop("warnings", UNSET))

        ui_motion_export_out = cls(
            artifact_id=artifact_id,
            document_id=document_id,
            duration_ms=duration_ms,
            frames=frames,
            height=height,
            mime=mime,
            name=name,
            size=size,
            slug=slug,
            url=url,
            version=version,
            width=width,
            ok=ok,
            replayed=replayed,
            warnings=warnings,
        )

        ui_motion_export_out.additional_properties = d
        return ui_motion_export_out

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
