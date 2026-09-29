from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.self_profile_in_backup_runtime_type_0 import (
    SelfProfileInBackupRuntimeType0,
)
from ..models.self_profile_in_runtime_type_0 import SelfProfileInRuntimeType0
from ..types import UNSET, Unset

T = TypeVar("T", bound="SelfProfileIn")


@_attrs_define
class SelfProfileIn:
    """
    Attributes:
        expected_version (int):
        backup_model (None | str | Unset):
        backup_runtime (None | SelfProfileInBackupRuntimeType0 | Unset):
        description (None | str | Unset):
        model (None | str | Unset):
        prompt (None | str | Unset):
        runtime (None | SelfProfileInRuntimeType0 | Unset):
    """

    expected_version: int
    backup_model: None | str | Unset = UNSET
    backup_runtime: None | SelfProfileInBackupRuntimeType0 | Unset = UNSET
    description: None | str | Unset = UNSET
    model: None | str | Unset = UNSET
    prompt: None | str | Unset = UNSET
    runtime: None | SelfProfileInRuntimeType0 | Unset = UNSET

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
        if description is not UNSET:
            field_dict["description"] = description
        if model is not UNSET:
            field_dict["model"] = model
        if prompt is not UNSET:
            field_dict["prompt"] = prompt
        if runtime is not UNSET:
            field_dict["runtime"] = runtime

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
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

        self_profile_in = cls(
            expected_version=expected_version,
            backup_model=backup_model,
            backup_runtime=backup_runtime,
            description=description,
            model=model,
            prompt=prompt,
            runtime=runtime,
        )

        return self_profile_in
