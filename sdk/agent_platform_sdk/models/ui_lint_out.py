from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.ui_lint_finding import UiLintFinding
    from ..models.ui_lint_out_by_severity import UiLintOutBySeverity


T = TypeVar("T", bound="UiLintOut")


@_attrs_define
class UiLintOut:
    """
    Attributes:
        by_severity (UiLintOutBySeverity):
        findings (list[UiLintFinding]):
        rules_version (str):
        slug (str):
        total (int):
        version (int):
        next_cursor (None | str | Unset):
        truncated (bool | Unset):  Default: False.
    """

    by_severity: UiLintOutBySeverity
    findings: list[UiLintFinding]
    rules_version: str
    slug: str
    total: int
    version: int
    next_cursor: None | str | Unset = UNSET
    truncated: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        by_severity = self.by_severity.to_dict()

        findings = []
        for findings_item_data in self.findings:
            findings_item = findings_item_data.to_dict()
            findings.append(findings_item)

        rules_version = self.rules_version

        slug = self.slug

        total = self.total

        version = self.version

        next_cursor: None | str | Unset
        if isinstance(self.next_cursor, Unset):
            next_cursor = UNSET
        else:
            next_cursor = self.next_cursor

        truncated = self.truncated

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "by_severity": by_severity,
                "findings": findings,
                "rules_version": rules_version,
                "slug": slug,
                "total": total,
                "version": version,
            }
        )
        if next_cursor is not UNSET:
            field_dict["next_cursor"] = next_cursor
        if truncated is not UNSET:
            field_dict["truncated"] = truncated

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.ui_lint_finding import UiLintFinding
        from ..models.ui_lint_out_by_severity import UiLintOutBySeverity

        d = dict(src_dict)
        by_severity = UiLintOutBySeverity.from_dict(d.pop("by_severity"))

        findings = []
        _findings = d.pop("findings")
        for findings_item_data in _findings:
            findings_item = UiLintFinding.from_dict(findings_item_data)

            findings.append(findings_item)

        rules_version = d.pop("rules_version")

        slug = d.pop("slug")

        total = d.pop("total")

        version = d.pop("version")

        def _parse_next_cursor(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        next_cursor = _parse_next_cursor(d.pop("next_cursor", UNSET))

        truncated = d.pop("truncated", UNSET)

        ui_lint_out = cls(
            by_severity=by_severity,
            findings=findings,
            rules_version=rules_version,
            slug=slug,
            total=total,
            version=version,
            next_cursor=next_cursor,
            truncated=truncated,
        )

        ui_lint_out.additional_properties = d
        return ui_lint_out

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
