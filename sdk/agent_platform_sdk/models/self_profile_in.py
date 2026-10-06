from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.self_profile_in_backup_runtime_type_0 import (
    SelfProfileInBackupRuntimeType0,
)
from ..models.self_profile_in_runtime_type_0 import SelfProfileInRuntimeType0
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.cron_entry import CronEntry


T = TypeVar("T", bound="SelfProfileIn")


@_attrs_define
class SelfProfileIn:
    """
    Attributes:
        expected_version (int):
        backup_model (None | str | Unset):
        backup_runtime (None | SelfProfileInBackupRuntimeType0 | Unset):
        crons (list[CronEntry] | None | Unset):
        description (None | str | Unset):
        model (None | str | Unset):
        prompt (None | str | Unset):
        runtime (None | SelfProfileInRuntimeType0 | Unset):
        timezone (None | str | Unset):
    """

    expected_version: int
    backup_model: None | str | Unset = UNSET
    backup_runtime: None | SelfProfileInBackupRuntimeType0 | Unset = UNSET
    crons: list[CronEntry] | None | Unset = UNSET
    description: None | str | Unset = UNSET
    model: None | str | Unset = UNSET
    prompt: None | str | Unset = UNSET
    runtime: None | SelfProfileInRuntimeType0 | Unset = UNSET
    timezone: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        expected_version = self.expected_version

        backup_model: None | str | Unset
        if isinstance(self.backup_model, Unset):
            backup_model = UNSET
        else:
            backup_model = self.backup_model

        backup_runtime: None | str | Unset
        if isinstance(self.backup_runtime, Unset):
            backup_runtime = UNSET
        elif isinstance(self.backup_runtime, SelfProfileInBackupRuntimeType0):
            backup_runtime = self.backup_runtime.value
        else:
            backup_runtime = self.backup_runtime

        crons: list[dict[str, Any]] | None | Unset
        if isinstance(self.crons, Unset):
            crons = UNSET
        elif isinstance(self.crons, list):
            crons = []
            for crons_type_0_item_data in self.crons:
                crons_type_0_item = crons_type_0_item_data.to_dict()
                crons.append(crons_type_0_item)

        else:
            crons = self.crons

        description: None | str | Unset
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        model: None | str | Unset
        if isinstance(self.model, Unset):
            model = UNSET
        else:
            model = self.model

        prompt: None | str | Unset
        if isinstance(self.prompt, Unset):
            prompt = UNSET
        else:
            prompt = self.prompt

        runtime: None | str | Unset
        if isinstance(self.runtime, Unset):
            runtime = UNSET
        elif isinstance(self.runtime, SelfProfileInRuntimeType0):
            runtime = self.runtime.value
        else:
            runtime = self.runtime

        timezone: None | str | Unset
        if isinstance(self.timezone, Unset):
            timezone = UNSET
        else:
            timezone = self.timezone

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "expected_version": expected_version,
            }
        )
        if backup_model is not UNSET:
            field_dict["backup_model"] = backup_model
        if backup_runtime is not UNSET:
            field_dict["backup_runtime"] = backup_runtime
        if crons is not UNSET:
            field_dict["crons"] = crons
        if description is not UNSET:
            field_dict["description"] = description
        if model is not UNSET:
            field_dict["model"] = model
        if prompt is not UNSET:
            field_dict["prompt"] = prompt
        if runtime is not UNSET:
            field_dict["runtime"] = runtime
        if timezone is not UNSET:
            field_dict["timezone"] = timezone

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.cron_entry import CronEntry

        d = dict(src_dict)
        expected_version = d.pop("expected_version")

        def _parse_backup_model(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        backup_model = _parse_backup_model(d.pop("backup_model", UNSET))

        def _parse_backup_runtime(
            data: object,
        ) -> None | SelfProfileInBackupRuntimeType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                backup_runtime_type_0 = SelfProfileInBackupRuntimeType0(data)

                return backup_runtime_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | SelfProfileInBackupRuntimeType0 | Unset, data)

        backup_runtime = _parse_backup_runtime(d.pop("backup_runtime", UNSET))

        def _parse_crons(data: object) -> list[CronEntry] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                crons_type_0 = []
                _crons_type_0 = data
                for crons_type_0_item_data in _crons_type_0:
                    crons_type_0_item = CronEntry.from_dict(crons_type_0_item_data)

                    crons_type_0.append(crons_type_0_item)

                return crons_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[CronEntry] | None | Unset, data)

        crons = _parse_crons(d.pop("crons", UNSET))

        def _parse_description(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        description = _parse_description(d.pop("description", UNSET))

        def _parse_model(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        model = _parse_model(d.pop("model", UNSET))

        def _parse_prompt(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        prompt = _parse_prompt(d.pop("prompt", UNSET))

        def _parse_runtime(data: object) -> None | SelfProfileInRuntimeType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                runtime_type_0 = SelfProfileInRuntimeType0(data)

                return runtime_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | SelfProfileInRuntimeType0 | Unset, data)

        runtime = _parse_runtime(d.pop("runtime", UNSET))

        def _parse_timezone(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        timezone = _parse_timezone(d.pop("timezone", UNSET))

        self_profile_in = cls(
            expected_version=expected_version,
            backup_model=backup_model,
            backup_runtime=backup_runtime,
            crons=crons,
            description=description,
            model=model,
            prompt=prompt,
            runtime=runtime,
            timezone=timezone,
        )

        return self_profile_in
