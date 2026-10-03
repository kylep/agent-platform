from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.user_out_kind import UserOutKind

if TYPE_CHECKING:
    from ..models.group_ref import GroupRef


T = TypeVar("T", bound="UserOut")


@_attrs_define
class UserOut:
    """One account in the admin's list. Never carries the password hash.

    Attributes:
        created_at (datetime.datetime | None):
        group (GroupRef | None):
        id (str):
        kind (UserOutKind):
        username (str):
    """

    created_at: datetime.datetime | None
    group: GroupRef | None
    id: str
    kind: UserOutKind
    username: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.group_ref import GroupRef

        created_at: None | str
        if isinstance(self.created_at, datetime.datetime):
            created_at = self.created_at.isoformat()
        else:
            created_at = self.created_at

        group: dict[str, Any] | None
        if isinstance(self.group, GroupRef):
            group = self.group.to_dict()
        else:
            group = self.group

        id = self.id

        kind = self.kind.value

        username = self.username

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "group": group,
                "id": id,
                "kind": kind,
                "username": username,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.group_ref import GroupRef

        d = dict(src_dict)

        def _parse_created_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                created_at_type_0 = datetime.datetime.fromisoformat(data)

                return created_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        created_at = _parse_created_at(d.pop("created_at"))

        def _parse_group(data: object) -> GroupRef | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                group_type_0 = GroupRef.from_dict(data)

                return group_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(GroupRef | None, data)

        group = _parse_group(d.pop("group"))

        id = d.pop("id")

        kind = UserOutKind(d.pop("kind"))

        username = d.pop("username")

        user_out = cls(
            created_at=created_at,
            group=group,
            id=id,
            kind=kind,
            username=username,
        )

        user_out.additional_properties = d
        return user_out

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
