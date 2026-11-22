from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.ui_sharing_principal import UiSharingPrincipal
    from ..models.ui_sharing_team import UiSharingTeam


T = TypeVar("T", bound="UiSharingChoices")


@_attrs_define
class UiSharingChoices:
    """
    Attributes:
        principals (list[UiSharingPrincipal]):
        teams (list[UiSharingTeam]):
        truncated (bool):
    """

    principals: list[UiSharingPrincipal]
    teams: list[UiSharingTeam]
    truncated: bool
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        principals = []
        for principals_item_data in self.principals:
            principals_item = principals_item_data.to_dict()
            principals.append(principals_item)

        teams = []
        for teams_item_data in self.teams:
            teams_item = teams_item_data.to_dict()
            teams.append(teams_item)

        truncated = self.truncated

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "principals": principals,
                "teams": teams,
                "truncated": truncated,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.ui_sharing_principal import UiSharingPrincipal
        from ..models.ui_sharing_team import UiSharingTeam

        d = dict(src_dict)
        principals = []
        _principals = d.pop("principals")
        for principals_item_data in _principals:
            principals_item = UiSharingPrincipal.from_dict(principals_item_data)

            principals.append(principals_item)

        teams = []
        _teams = d.pop("teams")
        for teams_item_data in _teams:
            teams_item = UiSharingTeam.from_dict(teams_item_data)

            teams.append(teams_item)

        truncated = d.pop("truncated")

        ui_sharing_choices = cls(
            principals=principals,
            teams=teams,
            truncated=truncated,
        )

        ui_sharing_choices.additional_properties = d
        return ui_sharing_choices

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
