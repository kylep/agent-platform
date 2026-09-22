from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="PullRequestFile")


@_attrs_define
class PullRequestFile:
    """
    Attributes:
        additions (int):
        deletions (int):
        filename (str):
        patch (None | str):
        status (str):
    """

    additions: int
    deletions: int
    filename: str
    patch: None | str
    status: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        additions = self.additions

        deletions = self.deletions

        filename = self.filename

        patch: None | str
        patch = self.patch

        status = self.status

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "additions": additions,
                "deletions": deletions,
                "filename": filename,
                "patch": patch,
                "status": status,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        additions = d.pop("additions")

        deletions = d.pop("deletions")

        filename = d.pop("filename")

        def _parse_patch(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        patch = _parse_patch(d.pop("patch"))

        status = d.pop("status")

        pull_request_file = cls(
            additions=additions,
            deletions=deletions,
            filename=filename,
            patch=patch,
            status=status,
        )

        pull_request_file.additional_properties = d
        return pull_request_file

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
