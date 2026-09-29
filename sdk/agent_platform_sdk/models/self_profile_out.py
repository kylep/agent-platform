from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="SelfProfileOut")


@_attrs_define
class SelfProfileOut:
    """
    Attributes:
        description (str):
        image_artifact_id (None | str):
        model (str):
        name (str):
        prompt (str):
        runtime (str):
        system_source (None | str):
        version (int):
    """

    description: str
    image_artifact_id: None | str
    model: str
    name: str
    prompt: str
    runtime: str
    system_source: None | str
    version: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        description = self.description

        image_artifact_id: None | str
        image_artifact_id = self.image_artifact_id

        model = self.model

        name = self.name

        prompt = self.prompt

        runtime = self.runtime

        system_source: None | str
        system_source = self.system_source

        version = self.version

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "description": description,
                "image_artifact_id": image_artifact_id,
                "model": model,
                "name": name,
                "prompt": prompt,
                "runtime": runtime,
                "system_source": system_source,
                "version": version,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        description = d.pop("description")

        def _parse_image_artifact_id(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        image_artifact_id = _parse_image_artifact_id(d.pop("image_artifact_id"))

        model = d.pop("model")

        name = d.pop("name")

        prompt = d.pop("prompt")

        runtime = d.pop("runtime")

        def _parse_system_source(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        system_source = _parse_system_source(d.pop("system_source"))

        version = d.pop("version")

        self_profile_out = cls(
            description=description,
            image_artifact_id=image_artifact_id,
            model=model,
            name=name,
            prompt=prompt,
            runtime=runtime,
            system_source=system_source,
            version=version,
        )

        self_profile_out.additional_properties = d
        return self_profile_out

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
