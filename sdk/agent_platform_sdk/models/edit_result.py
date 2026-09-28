from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.pr_ref import PrRef


T = TypeVar("T", bound="EditResult")


@_attrs_define
class EditResult:
    """
    Attributes:
        branch (None | str):
        changes (list[str]):
        pr (None | PrRef):
        sha (None | str):
        tier (int):
    """

    branch: None | str
    changes: list[str]
    pr: None | PrRef
    sha: None | str
    tier: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.pr_ref import PrRef

        branch: None | str
        branch = self.branch

        changes = self.changes

        pr: dict[str, Any] | None
        if isinstance(self.pr, PrRef):
            pr = self.pr.to_dict()
        else:
            pr = self.pr

        sha: None | str
        sha = self.sha

        tier = self.tier

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "branch": branch,
                "changes": changes,
                "pr": pr,
                "sha": sha,
                "tier": tier,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.pr_ref import PrRef

        d = dict(src_dict)

        def _parse_branch(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        branch = _parse_branch(d.pop("branch"))

        changes = cast(list[str], d.pop("changes"))

        def _parse_pr(data: object) -> None | PrRef:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                pr_type_0 = PrRef.from_dict(data)

                return pr_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | PrRef, data)

        pr = _parse_pr(d.pop("pr"))

        def _parse_sha(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        sha = _parse_sha(d.pop("sha"))

        tier = d.pop("tier")

        edit_result = cls(
            branch=branch,
            changes=changes,
            pr=pr,
            sha=sha,
            tier=tier,
        )

        edit_result.additional_properties = d
        return edit_result

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
