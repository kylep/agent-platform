from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="CodexGeneratedImage")


@_attrs_define
class CodexGeneratedImage:
    """
    Attributes:
        content_b64 (str):
        mime (None | str | Unset):
        name (str | Unset):  Default: 'codex-image.png'.
    """

    content_b64: str
    mime: None | str | Unset = UNSET
    name: str | Unset = "codex-image.png"
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        content_b64 = self.content_b64

        mime: None | str | Unset
        if isinstance(self.mime, Unset):
            mime = UNSET
        else:
            mime = self.mime

        name = self.name

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "content_b64": content_b64,
            }
        )
        if mime is not UNSET:
            field_dict["mime"] = mime
        if name is not UNSET:
            field_dict["name"] = name

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        content_b64 = d.pop("content_b64")

        def _parse_mime(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        mime = _parse_mime(d.pop("mime", UNSET))

        name = d.pop("name", UNSET)

        codex_generated_image = cls(
            content_b64=content_b64,
            mime=mime,
            name=name,
        )

        codex_generated_image.additional_properties = d
        return codex_generated_image

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
