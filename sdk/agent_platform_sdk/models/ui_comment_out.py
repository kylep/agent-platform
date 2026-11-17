from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.ui_comment import UiComment
    from ..models.ui_comment_notification import UiCommentNotification


T = TypeVar("T", bound="UiCommentOut")


@_attrs_define
class UiCommentOut:
    """
    Attributes:
        slug (str):
        comment (None | UiComment | Unset):
        document_id (None | str | Unset):
        id (None | str | Unset):
        notifications (list[UiCommentNotification] | Unset):
        ok (bool | Unset):  Default: True.
        replayed (bool | Unset):  Default: False.
        resolved (bool | None | Unset):
    """

    slug: str
    comment: None | UiComment | Unset = UNSET
    document_id: None | str | Unset = UNSET
    id: None | str | Unset = UNSET
    notifications: list[UiCommentNotification] | Unset = UNSET
    ok: bool | Unset = True
    replayed: bool | Unset = False
    resolved: bool | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.ui_comment import UiComment

        slug = self.slug

        comment: dict[str, Any] | None | Unset
        if isinstance(self.comment, Unset):
            comment = UNSET
        elif isinstance(self.comment, UiComment):
            comment = self.comment.to_dict()
        else:
            comment = self.comment

        document_id: None | str | Unset
        if isinstance(self.document_id, Unset):
            document_id = UNSET
        else:
            document_id = self.document_id

        id: None | str | Unset
        if isinstance(self.id, Unset):
            id = UNSET
        else:
            id = self.id

        notifications: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.notifications, Unset):
            notifications = []
            for notifications_item_data in self.notifications:
                notifications_item = notifications_item_data.to_dict()
                notifications.append(notifications_item)

        ok = self.ok

        replayed = self.replayed

        resolved: bool | None | Unset
        if isinstance(self.resolved, Unset):
            resolved = UNSET
        else:
            resolved = self.resolved

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "slug": slug,
            }
        )
        if comment is not UNSET:
            field_dict["comment"] = comment
        if document_id is not UNSET:
            field_dict["document_id"] = document_id
        if id is not UNSET:
            field_dict["id"] = id
        if notifications is not UNSET:
            field_dict["notifications"] = notifications
        if ok is not UNSET:
            field_dict["ok"] = ok
        if replayed is not UNSET:
            field_dict["replayed"] = replayed
        if resolved is not UNSET:
            field_dict["resolved"] = resolved

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.ui_comment import UiComment
        from ..models.ui_comment_notification import UiCommentNotification

        d = dict(src_dict)
        slug = d.pop("slug")

        def _parse_comment(data: object) -> None | UiComment | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                comment_type_0 = UiComment.from_dict(data)

                return comment_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | UiComment | Unset, data)

        comment = _parse_comment(d.pop("comment", UNSET))

        def _parse_document_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        document_id = _parse_document_id(d.pop("document_id", UNSET))

        def _parse_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        id = _parse_id(d.pop("id", UNSET))

        _notifications = d.pop("notifications", UNSET)
        notifications: list[UiCommentNotification] | Unset = UNSET
        if _notifications is not UNSET:
            notifications = []
            for notifications_item_data in _notifications:
                notifications_item = UiCommentNotification.from_dict(
                    notifications_item_data
                )

                notifications.append(notifications_item)

        ok = d.pop("ok", UNSET)

        replayed = d.pop("replayed", UNSET)

        def _parse_resolved(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        resolved = _parse_resolved(d.pop("resolved", UNSET))

        ui_comment_out = cls(
            slug=slug,
            comment=comment,
            document_id=document_id,
            id=id,
            notifications=notifications,
            ok=ok,
            replayed=replayed,
            resolved=resolved,
        )

        ui_comment_out.additional_properties = d
        return ui_comment_out

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
