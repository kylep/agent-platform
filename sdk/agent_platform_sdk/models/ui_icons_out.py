from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.ui_icon_source import UiIconSource
    from ..models.ui_icon_summary import UiIconSummary


T = TypeVar("T", bound="UiIconsOut")


@_attrs_define
class UiIconsOut:
    """
    Attributes:
        icons (list[UiIconSummary]):
        revision (int):
        source (UiIconSource):
        total (int):
        next_cursor (None | str | Unset):
    """

    icons: list[UiIconSummary]
    revision: int
    source: UiIconSource
    total: int
    next_cursor: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        icons = []
        for icons_item_data in self.icons:
            icons_item = icons_item_data.to_dict()
            icons.append(icons_item)

        revision = self.revision

        source = self.source.to_dict()

        total = self.total

        next_cursor: None | str | Unset
        if isinstance(self.next_cursor, Unset):
            next_cursor = UNSET
        else:
            next_cursor = self.next_cursor

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "icons": icons,
                "revision": revision,
                "source": source,
                "total": total,
            }
        )
        if next_cursor is not UNSET:
            field_dict["next_cursor"] = next_cursor

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.ui_icon_source import UiIconSource
        from ..models.ui_icon_summary import UiIconSummary

        d = dict(src_dict)
        icons = []
        _icons = d.pop("icons")
        for icons_item_data in _icons:
            icons_item = UiIconSummary.from_dict(icons_item_data)

            icons.append(icons_item)

        revision = d.pop("revision")

        source = UiIconSource.from_dict(d.pop("source"))

        total = d.pop("total")

        def _parse_next_cursor(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        next_cursor = _parse_next_cursor(d.pop("next_cursor", UNSET))

        ui_icons_out = cls(
            icons=icons,
            revision=revision,
            source=source,
            total=total,
            next_cursor=next_cursor,
        )

        ui_icons_out.additional_properties = d
        return ui_icons_out

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
