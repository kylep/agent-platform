from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="ImageModel")


@_attrs_define
class ImageModel:
    """A registry entry × whether its provider's key is set: what the Studio's
    model select shows greyed or live. `sizes` or `aspects`, never both — which
    one says what geometry the model takes.

        Attributes:
            aspects (list[str] | None):
            configured (bool):
            custom_size (bool):
            default (bool):
            edits (bool):
            id (str):
            label (str):
            price_usd (float):
            provider (str):
            qualities (list[str] | None):
            sizes (list[str] | None):
    """

    aspects: list[str] | None
    configured: bool
    custom_size: bool
    default: bool
    edits: bool
    id: str
    label: str
    price_usd: float
    provider: str
    qualities: list[str] | None
    sizes: list[str] | None
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        aspects: list[str] | None
        if isinstance(self.aspects, list):
            aspects = self.aspects

        else:
            aspects = self.aspects

        configured = self.configured

        custom_size = self.custom_size

        default = self.default

        edits = self.edits

        id = self.id

        label = self.label

        price_usd = self.price_usd

        provider = self.provider

        qualities: list[str] | None
        if isinstance(self.qualities, list):
            qualities = self.qualities

        else:
            qualities = self.qualities

        sizes: list[str] | None
        if isinstance(self.sizes, list):
            sizes = self.sizes

        else:
            sizes = self.sizes

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "aspects": aspects,
                "configured": configured,
                "custom_size": custom_size,
                "default": default,
                "edits": edits,
                "id": id,
                "label": label,
                "price_usd": price_usd,
                "provider": provider,
                "qualities": qualities,
                "sizes": sizes,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)

        def _parse_aspects(data: object) -> list[str] | None:
            if data is None:
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                aspects_type_0 = cast(list[str], data)

                return aspects_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[str] | None, data)

        aspects = _parse_aspects(d.pop("aspects"))

        configured = d.pop("configured")

        custom_size = d.pop("custom_size")

        default = d.pop("default")

        edits = d.pop("edits")

        id = d.pop("id")

        label = d.pop("label")

        price_usd = d.pop("price_usd")

        provider = d.pop("provider")

        def _parse_qualities(data: object) -> list[str] | None:
            if data is None:
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                qualities_type_0 = cast(list[str], data)

                return qualities_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[str] | None, data)

        qualities = _parse_qualities(d.pop("qualities"))

        def _parse_sizes(data: object) -> list[str] | None:
            if data is None:
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                sizes_type_0 = cast(list[str], data)

                return sizes_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[str] | None, data)

        sizes = _parse_sizes(d.pop("sizes"))

        image_model = cls(
            aspects=aspects,
            configured=configured,
            custom_size=custom_size,
            default=default,
            edits=edits,
            id=id,
            label=label,
            price_usd=price_usd,
            provider=provider,
            qualities=qualities,
            sizes=sizes,
        )

        image_model.additional_properties = d
        return image_model

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
