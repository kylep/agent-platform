from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.ui_icon_path import UiIconPath
    from ..models.ui_icon_source import UiIconSource


T = TypeVar("T", bound="UiIconOut")


@_attrs_define
class UiIconOut:
    """
    Attributes:
        categories (list[str]):
        d (str):
        id (str):
        name (str):
        paths (list[UiIconPath]):
        revision (int):
        source (UiIconSource):
        tags (list[str]):
        view_box (list[float]):
    """

    categories: list[str]
    d: str
    id: str
    name: str
    paths: list[UiIconPath]
    revision: int
    source: UiIconSource
    tags: list[str]
    view_box: list[float]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        categories = self.categories

        d = self.d

        id = self.id

        name = self.name

        paths = []
        for paths_item_data in self.paths:
            paths_item = paths_item_data.to_dict()
            paths.append(paths_item)

        revision = self.revision

        source = self.source.to_dict()

        tags = self.tags

        view_box = self.view_box

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "categories": categories,
                "d": d,
                "id": id,
                "name": name,
                "paths": paths,
                "revision": revision,
                "source": source,
                "tags": tags,
                "view_box": view_box,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.ui_icon_path import UiIconPath
        from ..models.ui_icon_source import UiIconSource

        d = dict(src_dict)
        categories = cast(list[str], d.pop("categories"))

        d = d.pop("d")

        id = d.pop("id")

        name = d.pop("name")

        paths = []
        _paths = d.pop("paths")
        for paths_item_data in _paths:
            paths_item = UiIconPath.from_dict(paths_item_data)

            paths.append(paths_item)

        revision = d.pop("revision")

        source = UiIconSource.from_dict(d.pop("source"))

        tags = cast(list[str], d.pop("tags"))

        view_box = cast(list[float], d.pop("view_box"))

        ui_icon_out = cls(
            categories=categories,
            d=d,
            id=id,
            name=name,
            paths=paths,
            revision=revision,
            source=source,
            tags=tags,
            view_box=view_box,
        )

        ui_icon_out.additional_properties = d
        return ui_icon_out

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
