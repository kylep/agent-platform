from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.ui_platform_implementation_status import UiPlatformImplementationStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.ui_platform_implementation_fixture import (
        UiPlatformImplementationFixture,
    )
    from ..models.ui_platform_implementation_props import UiPlatformImplementationProps


T = TypeVar("T", bound="UiPlatformImplementation")


@_attrs_define
class UiPlatformImplementation:
    """
    Attributes:
        component (str):
        fixture (UiPlatformImplementationFixture):
        import_path (str):
        limitations (list[str]):
        props (UiPlatformImplementationProps):
        revision (int):
        status (UiPlatformImplementationStatus):
        snippet (None | str | Unset):
        story_id (None | str | Unset):
        variant (None | str | Unset):
    """

    component: str
    fixture: UiPlatformImplementationFixture
    import_path: str
    limitations: list[str]
    props: UiPlatformImplementationProps
    revision: int
    status: UiPlatformImplementationStatus
    snippet: None | str | Unset = UNSET
    story_id: None | str | Unset = UNSET
    variant: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        component = self.component

        fixture = self.fixture.to_dict()

        import_path = self.import_path

        limitations = self.limitations

        props = self.props.to_dict()

        revision = self.revision

        status = self.status.value

        snippet: None | str | Unset
        if isinstance(self.snippet, Unset):
            snippet = UNSET
        else:
            snippet = self.snippet

        story_id: None | str | Unset
        if isinstance(self.story_id, Unset):
            story_id = UNSET
        else:
            story_id = self.story_id

        variant: None | str | Unset
        if isinstance(self.variant, Unset):
            variant = UNSET
        else:
            variant = self.variant

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "component": component,
                "fixture": fixture,
                "import_path": import_path,
                "limitations": limitations,
                "props": props,
                "revision": revision,
                "status": status,
            }
        )
        if snippet is not UNSET:
            field_dict["snippet"] = snippet
        if story_id is not UNSET:
            field_dict["story_id"] = story_id
        if variant is not UNSET:
            field_dict["variant"] = variant

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.ui_platform_implementation_fixture import (
            UiPlatformImplementationFixture,
        )
        from ..models.ui_platform_implementation_props import (
            UiPlatformImplementationProps,
        )

        d = dict(src_dict)
        component = d.pop("component")

        fixture = UiPlatformImplementationFixture.from_dict(d.pop("fixture"))

        import_path = d.pop("import_path")

        limitations = cast(list[str], d.pop("limitations"))

        props = UiPlatformImplementationProps.from_dict(d.pop("props"))

        revision = d.pop("revision")

        status = UiPlatformImplementationStatus(d.pop("status"))

        def _parse_snippet(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        snippet = _parse_snippet(d.pop("snippet", UNSET))

        def _parse_story_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        story_id = _parse_story_id(d.pop("story_id", UNSET))

        def _parse_variant(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        variant = _parse_variant(d.pop("variant", UNSET))

        ui_platform_implementation = cls(
            component=component,
            fixture=fixture,
            import_path=import_path,
            limitations=limitations,
            props=props,
            revision=revision,
            status=status,
            snippet=snippet,
            story_id=story_id,
            variant=variant,
        )

        ui_platform_implementation.additional_properties = d
        return ui_platform_implementation

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
