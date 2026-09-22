from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="GenerateIn")


@_attrs_define
class GenerateIn:
    """`model` defaults to the registry's default; `size` or `aspect` is
    bridged by the tool when the model takes the other; `reference_ids` are
    artifacts the caller can read, images only, at most four.

        Attributes:
            prompt (str):
            aspect (None | str | Unset):
            model (None | str | Unset):
            name (None | str | Unset):
            quality (None | str | Unset):
            reference_ids (list[str] | None | Unset):
            seed (int | None | Unset):
            size (None | str | Unset):
            tags (list[str] | None | Unset):
    """

    prompt: str
    aspect: None | str | Unset = UNSET
    model: None | str | Unset = UNSET
    name: None | str | Unset = UNSET
    quality: None | str | Unset = UNSET
    reference_ids: list[str] | None | Unset = UNSET
    seed: int | None | Unset = UNSET
    size: None | str | Unset = UNSET
    tags: list[str] | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        prompt = self.prompt

        aspect: None | str | Unset
        if isinstance(self.aspect, Unset):
            aspect = UNSET
        else:
            aspect = self.aspect

        model: None | str | Unset
        if isinstance(self.model, Unset):
            model = UNSET
        else:
            model = self.model

        name: None | str | Unset
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

        quality: None | str | Unset
        if isinstance(self.quality, Unset):
            quality = UNSET
        else:
            quality = self.quality

        reference_ids: list[str] | None | Unset
        if isinstance(self.reference_ids, Unset):
            reference_ids = UNSET
        elif isinstance(self.reference_ids, list):
            reference_ids = self.reference_ids

        else:
            reference_ids = self.reference_ids

        seed: int | None | Unset
        if isinstance(self.seed, Unset):
            seed = UNSET
        else:
            seed = self.seed

        size: None | str | Unset
        if isinstance(self.size, Unset):
            size = UNSET
        else:
            size = self.size

        tags: list[str] | None | Unset
        if isinstance(self.tags, Unset):
            tags = UNSET
        elif isinstance(self.tags, list):
            tags = self.tags

        else:
            tags = self.tags

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "prompt": prompt,
            }
        )
        if aspect is not UNSET:
            field_dict["aspect"] = aspect
        if model is not UNSET:
            field_dict["model"] = model
        if name is not UNSET:
            field_dict["name"] = name
        if quality is not UNSET:
            field_dict["quality"] = quality
        if reference_ids is not UNSET:
            field_dict["reference_ids"] = reference_ids
        if seed is not UNSET:
            field_dict["seed"] = seed
        if size is not UNSET:
            field_dict["size"] = size
        if tags is not UNSET:
            field_dict["tags"] = tags

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        prompt = d.pop("prompt")

        def _parse_aspect(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        aspect = _parse_aspect(d.pop("aspect", UNSET))

        def _parse_model(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        model = _parse_model(d.pop("model", UNSET))

        def _parse_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        name = _parse_name(d.pop("name", UNSET))

        def _parse_quality(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        quality = _parse_quality(d.pop("quality", UNSET))

        def _parse_reference_ids(data: object) -> list[str] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                reference_ids_type_0 = cast(list[str], data)

                return reference_ids_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[str] | None | Unset, data)

        reference_ids = _parse_reference_ids(d.pop("reference_ids", UNSET))

        def _parse_seed(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        seed = _parse_seed(d.pop("seed", UNSET))

        def _parse_size(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        size = _parse_size(d.pop("size", UNSET))

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

        generate_in = cls(
            prompt=prompt,
            aspect=aspect,
            model=model,
            name=name,
            quality=quality,
            reference_ids=reference_ids,
            seed=seed,
            size=size,
            tags=tags,
        )

        generate_in.additional_properties = d
        return generate_in

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
