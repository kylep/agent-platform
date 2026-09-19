from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.workbench_pr import WorkbenchPr


T = TypeVar("T", bound="PublishOut")


@_attrs_define
class PublishOut:
    """
    Attributes:
        branch (str):
        paths (list[str]):
        tests_removed (list[str]):
        auto_merge (bool | Unset):  Default: False.
        pr (None | Unset | WorkbenchPr):
        ticket_state (None | str | Unset):
        verify_ok (bool | None | Unset):
        warnings (list[str] | Unset):
    """

    branch: str
    paths: list[str]
    tests_removed: list[str]
    auto_merge: bool | Unset = False
    pr: None | Unset | WorkbenchPr = UNSET
    ticket_state: None | str | Unset = UNSET
    verify_ok: bool | None | Unset = UNSET
    warnings: list[str] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.workbench_pr import WorkbenchPr

        branch = self.branch

        paths = self.paths

        tests_removed = self.tests_removed

        auto_merge = self.auto_merge

        pr: dict[str, Any] | None | Unset
        if isinstance(self.pr, Unset):
            pr = UNSET
        elif isinstance(self.pr, WorkbenchPr):
            pr = self.pr.to_dict()
        else:
            pr = self.pr

        ticket_state: None | str | Unset
        if isinstance(self.ticket_state, Unset):
            ticket_state = UNSET
        else:
            ticket_state = self.ticket_state

        verify_ok: bool | None | Unset
        if isinstance(self.verify_ok, Unset):
            verify_ok = UNSET
        else:
            verify_ok = self.verify_ok

        warnings: list[str] | Unset = UNSET
        if not isinstance(self.warnings, Unset):
            warnings = self.warnings

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "branch": branch,
                "paths": paths,
                "tests_removed": tests_removed,
            }
        )
        if auto_merge is not UNSET:
            field_dict["auto_merge"] = auto_merge
        if pr is not UNSET:
            field_dict["pr"] = pr
        if ticket_state is not UNSET:
            field_dict["ticket_state"] = ticket_state
        if verify_ok is not UNSET:
            field_dict["verify_ok"] = verify_ok
        if warnings is not UNSET:
            field_dict["warnings"] = warnings

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.workbench_pr import WorkbenchPr

        d = dict(src_dict)
        branch = d.pop("branch")

        paths = cast(list[str], d.pop("paths"))

        tests_removed = cast(list[str], d.pop("tests_removed"))

        auto_merge = d.pop("auto_merge", UNSET)

        def _parse_pr(data: object) -> None | Unset | WorkbenchPr:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                pr_type_0 = WorkbenchPr.from_dict(data)

                return pr_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | Unset | WorkbenchPr, data)

        pr = _parse_pr(d.pop("pr", UNSET))

        def _parse_ticket_state(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        ticket_state = _parse_ticket_state(d.pop("ticket_state", UNSET))

        def _parse_verify_ok(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        verify_ok = _parse_verify_ok(d.pop("verify_ok", UNSET))

        warnings = cast(list[str], d.pop("warnings", UNSET))

        publish_out = cls(
            branch=branch,
            paths=paths,
            tests_removed=tests_removed,
            auto_merge=auto_merge,
            pr=pr,
            ticket_state=ticket_state,
            verify_ok=verify_ok,
            warnings=warnings,
        )

        publish_out.additional_properties = d
        return publish_out

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
