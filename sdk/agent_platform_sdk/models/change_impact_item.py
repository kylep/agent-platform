from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="ChangeImpactItem")


@_attrs_define
class ChangeImpactItem:
    """
    Attributes:
        additions (int):
        area (str):
        block (None | str):
        deletions (int):
        file (str):
        notable (list[str]):
        status (str):
    """

    additions: int
    area: str
    block: None | str
    deletions: int
    file: str
    notable: list[str]
    status: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        additions = self.additions

        area = self.area

        block: None | str
        block = self.block

        deletions = self.deletions

        file = self.file

        notable = self.notable

        status = self.status

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "additions": additions,
                "area": area,
                "block": block,
                "deletions": deletions,
                "file": file,
                "notable": notable,
                "status": status,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        additions = d.pop("additions")

        area = d.pop("area")

        def _parse_block(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        block = _parse_block(d.pop("block"))

        deletions = d.pop("deletions")

        file = d.pop("file")

        notable = cast(list[str], d.pop("notable"))

        status = d.pop("status")

        change_impact_item = cls(
            additions=additions,
            area=area,
            block=block,
            deletions=deletions,
            file=file,
            notable=notable,
            status=status,
        )

        change_impact_item.additional_properties = d
        return change_impact_item

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
