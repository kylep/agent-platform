from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="UiDocExportOut")


@_attrs_define
class UiDocExportOut:
    """
    Attributes:
        artifact_id (str):
        card (str):
        height (int):
        mime (str):
        name (str):
        size (int):
        slug (str):
        url (str):
        version (int):
        width (int):
        card_posted (bool | Unset):  Default: False.
        ok (bool | Unset):  Default: True.
    """

    artifact_id: str
    card: str
    height: int
    mime: str
    name: str
    size: int
    slug: str
    url: str
    version: int
    width: int
    card_posted: bool | Unset = False
    ok: bool | Unset = True
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        artifact_id = self.artifact_id

        card = self.card

        height = self.height

        mime = self.mime

        name = self.name

        size = self.size

        slug = self.slug

        url = self.url

        version = self.version

        width = self.width

        card_posted = self.card_posted

        ok = self.ok

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "artifact_id": artifact_id,
                "card": card,
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
        if card_posted is not UNSET:
            field_dict["card_posted"] = card_posted
        if ok is not UNSET:
            field_dict["ok"] = ok

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        artifact_id = d.pop("artifact_id")

        card = d.pop("card")

        height = d.pop("height")

        mime = d.pop("mime")

        name = d.pop("name")

        size = d.pop("size")

        slug = d.pop("slug")

        url = d.pop("url")

        version = d.pop("version")

        width = d.pop("width")

        card_posted = d.pop("card_posted", UNSET)

        ok = d.pop("ok", UNSET)

        ui_doc_export_out = cls(
            artifact_id=artifact_id,
            card=card,
            height=height,
            mime=mime,
            name=name,
            size=size,
            slug=slug,
            url=url,
            version=version,
            width=width,
            card_posted=card_posted,
            ok=ok,
        )

        ui_doc_export_out.additional_properties = d
        return ui_doc_export_out

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
