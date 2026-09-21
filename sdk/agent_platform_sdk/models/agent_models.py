from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.model_option import ModelOption


T = TypeVar("T", bound="AgentModels")


@_attrs_define
class AgentModels:
    """
    Attributes:
        models (list[ModelOption]):
        codex_models (list[ModelOption] | Unset):
    """

    models: list[ModelOption]
    codex_models: list[ModelOption] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        models = []
        for models_item_data in self.models:
            models_item = models_item_data.to_dict()
            models.append(models_item)

        codex_models: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.codex_models, Unset):
            codex_models = []
            for codex_models_item_data in self.codex_models:
                codex_models_item = codex_models_item_data.to_dict()
                codex_models.append(codex_models_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "models": models,
            }
        )
        if codex_models is not UNSET:
            field_dict["codex_models"] = codex_models

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.model_option import ModelOption

        d = dict(src_dict)
        models = []
        _models = d.pop("models")
        for models_item_data in _models:
            models_item = ModelOption.from_dict(models_item_data)

            models.append(models_item)

        _codex_models = d.pop("codex_models", UNSET)
        codex_models: list[ModelOption] | Unset = UNSET
        if _codex_models is not UNSET:
            codex_models = []
            for codex_models_item_data in _codex_models:
                codex_models_item = ModelOption.from_dict(codex_models_item_data)

                codex_models.append(codex_models_item)

        agent_models = cls(
            models=models,
            codex_models=codex_models,
        )

        agent_models.additional_properties = d
        return agent_models

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
