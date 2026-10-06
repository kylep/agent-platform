from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.cron_entry import CronEntry


T = TypeVar("T", bound="SelfProfileOut")


@_attrs_define
class SelfProfileOut:
    """
    Attributes:
        backup_model (str):
        backup_runtime (None | str):
        crons (list[CronEntry]):
        description (str):
        image_artifact_id (None | str):
        model (str):
        name (str):
        prompt (str):
        runtime (str):
        system_source (None | str):
        timezone (str):
        version (int):
    """

    backup_model: str
    backup_runtime: None | str
    crons: list[CronEntry]
    description: str
    image_artifact_id: None | str
    model: str
    name: str
    prompt: str
    runtime: str
    system_source: None | str
    timezone: str
    version: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        backup_model = self.backup_model

        backup_runtime: None | str
        backup_runtime = self.backup_runtime

        crons = []
        for crons_item_data in self.crons:
            crons_item = crons_item_data.to_dict()
            crons.append(crons_item)

        description = self.description

        image_artifact_id: None | str
        image_artifact_id = self.image_artifact_id

        model = self.model

        name = self.name

        prompt = self.prompt

        runtime = self.runtime

        system_source: None | str
        system_source = self.system_source

        timezone = self.timezone

        version = self.version

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "backup_model": backup_model,
                "backup_runtime": backup_runtime,
                "crons": crons,
                "description": description,
                "image_artifact_id": image_artifact_id,
                "model": model,
                "name": name,
                "prompt": prompt,
                "runtime": runtime,
                "system_source": system_source,
                "timezone": timezone,
                "version": version,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.cron_entry import CronEntry

        d = dict(src_dict)
        backup_model = d.pop("backup_model")

        def _parse_backup_runtime(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        backup_runtime = _parse_backup_runtime(d.pop("backup_runtime"))

        crons = []
        _crons = d.pop("crons")
        for crons_item_data in _crons:
            crons_item = CronEntry.from_dict(crons_item_data)

            crons.append(crons_item)

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

        timezone = d.pop("timezone")

        version = d.pop("version")

        self_profile_out = cls(
            backup_model=backup_model,
            backup_runtime=backup_runtime,
            crons=crons,
            description=description,
            image_artifact_id=image_artifact_id,
            model=model,
            name=name,
            prompt=prompt,
            runtime=runtime,
            system_source=system_source,
            timezone=timezone,
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
