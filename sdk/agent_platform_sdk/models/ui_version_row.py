from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="UiVersionRow")


@_attrs_define
class UiVersionRow:
    """
    Attributes:
        author (str):
        first_version (int):
        open_ (bool):
        range_ (str):
        version (int):
        created_at (None | str | Unset):
        label (None | str | Unset):
        last_write_at (None | str | Unset):
        reasons (list[str] | Unset):
        run_id (None | str | Unset):
    """

    author: str
    first_version: int
    open_: bool
    range_: str
    version: int
    created_at: None | str | Unset = UNSET
    label: None | str | Unset = UNSET
    last_write_at: None | str | Unset = UNSET
    reasons: list[str] | Unset = UNSET
    run_id: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        author = self.author

        first_version = self.first_version

        open_ = self.open_

        range_ = self.range_

        version = self.version

        created_at: None | str | Unset
        if isinstance(self.created_at, Unset):
            created_at = UNSET
        else:
            created_at = self.created_at

        label: None | str | Unset
        if isinstance(self.label, Unset):
            label = UNSET
        else:
            label = self.label

        last_write_at: None | str | Unset
        if isinstance(self.last_write_at, Unset):
            last_write_at = UNSET
        else:
            last_write_at = self.last_write_at

        reasons: list[str] | Unset = UNSET
        if not isinstance(self.reasons, Unset):
            reasons = self.reasons

        run_id: None | str | Unset
        if isinstance(self.run_id, Unset):
            run_id = UNSET
        else:
            run_id = self.run_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "author": author,
                "first_version": first_version,
                "open": open_,
                "range": range_,
                "version": version,
            }
        )
        if created_at is not UNSET:
            field_dict["created_at"] = created_at
        if label is not UNSET:
            field_dict["label"] = label
        if last_write_at is not UNSET:
            field_dict["last_write_at"] = last_write_at
        if reasons is not UNSET:
            field_dict["reasons"] = reasons
        if run_id is not UNSET:
            field_dict["run_id"] = run_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        author = d.pop("author")

        first_version = d.pop("first_version")

        open_ = d.pop("open")

        range_ = d.pop("range")

        version = d.pop("version")

        def _parse_created_at(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        created_at = _parse_created_at(d.pop("created_at", UNSET))

        def _parse_label(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        label = _parse_label(d.pop("label", UNSET))

        def _parse_last_write_at(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        last_write_at = _parse_last_write_at(d.pop("last_write_at", UNSET))

        reasons = cast(list[str], d.pop("reasons", UNSET))

        def _parse_run_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        run_id = _parse_run_id(d.pop("run_id", UNSET))

        ui_version_row = cls(
            author=author,
            first_version=first_version,
            open_=open_,
            range_=range_,
            version=version,
            created_at=created_at,
            label=label,
            last_write_at=last_write_at,
            reasons=reasons,
            run_id=run_id,
        )

        ui_version_row.additional_properties = d
        return ui_version_row

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
