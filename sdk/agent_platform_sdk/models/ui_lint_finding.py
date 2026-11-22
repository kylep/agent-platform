from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.ui_lint_finding_severity import UiLintFindingSeverity
from ..types import UNSET, Unset

T = TypeVar("T", bound="UiLintFinding")


@_attrs_define
class UiLintFinding:
    """
    Attributes:
        approximate (bool):
        code (str):
        message (str):
        severity (UiLintFindingSeverity):
        suggestion (str):
        mode (None | str | Unset):
        node_id (None | str | Unset):
    """

    approximate: bool
    code: str
    message: str
    severity: UiLintFindingSeverity
    suggestion: str
    mode: None | str | Unset = UNSET
    node_id: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        approximate = self.approximate

        code = self.code

        message = self.message

        severity = self.severity.value

        suggestion = self.suggestion

        mode: None | str | Unset
        if isinstance(self.mode, Unset):
            mode = UNSET
        else:
            mode = self.mode

        node_id: None | str | Unset
        if isinstance(self.node_id, Unset):
            node_id = UNSET
        else:
            node_id = self.node_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "approximate": approximate,
                "code": code,
                "message": message,
                "severity": severity,
                "suggestion": suggestion,
            }
        )
        if mode is not UNSET:
            field_dict["mode"] = mode
        if node_id is not UNSET:
            field_dict["node_id"] = node_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        approximate = d.pop("approximate")

        code = d.pop("code")

        message = d.pop("message")

        severity = UiLintFindingSeverity(d.pop("severity"))

        suggestion = d.pop("suggestion")

        def _parse_mode(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        mode = _parse_mode(d.pop("mode", UNSET))

        def _parse_node_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        node_id = _parse_node_id(d.pop("node_id", UNSET))

        ui_lint_finding = cls(
            approximate=approximate,
            code=code,
            message=message,
            severity=severity,
            suggestion=suggestion,
            mode=mode,
            node_id=node_id,
        )

        ui_lint_finding.additional_properties = d
        return ui_lint_finding

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
