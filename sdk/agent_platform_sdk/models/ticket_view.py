from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.relay_face import RelayFace


T = TypeVar("T", bound="TicketView")


@_attrs_define
class TicketView:
    """
    Attributes:
        assignee (None | str):
        body (str):
        channel_id (str):
        closed_at (None | str):
        created_at (None | str):
        due_at (None | str):
        id (str):
        key (str):
        labels (list[str]):
        last_activity_at (None | str):
        parent_id (None | str):
        priority (str):
        reporter (str):
        root_message_id (None | str):
        run_id (None | str):
        state (str):
        title (str):
        updated_at (None | str):
        assignee_face (None | RelayFace | Unset):
        reporter_face (None | RelayFace | Unset):
        stale (bool | Unset):  Default: False.
    """

    assignee: None | str
    body: str
    channel_id: str
    closed_at: None | str
    created_at: None | str
    due_at: None | str
    id: str
    key: str
    labels: list[str]
    last_activity_at: None | str
    parent_id: None | str
    priority: str
    reporter: str
    root_message_id: None | str
    run_id: None | str
    state: str
    title: str
    updated_at: None | str
    assignee_face: None | RelayFace | Unset = UNSET
    reporter_face: None | RelayFace | Unset = UNSET
    stale: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.relay_face import RelayFace

        assignee: None | str
        assignee = self.assignee

        body = self.body

        channel_id = self.channel_id

        closed_at: None | str
        closed_at = self.closed_at

        created_at: None | str
        created_at = self.created_at

        due_at: None | str
        due_at = self.due_at

        id = self.id

        key = self.key

        labels = self.labels

        last_activity_at: None | str
        last_activity_at = self.last_activity_at

        parent_id: None | str
        parent_id = self.parent_id

        priority = self.priority

        reporter = self.reporter

        root_message_id: None | str
        root_message_id = self.root_message_id

        run_id: None | str
        run_id = self.run_id

        state = self.state

        title = self.title

        updated_at: None | str
        updated_at = self.updated_at

        assignee_face: dict[str, Any] | None | Unset
        if isinstance(self.assignee_face, Unset):
            assignee_face = UNSET
        elif isinstance(self.assignee_face, RelayFace):
            assignee_face = self.assignee_face.to_dict()
        else:
            assignee_face = self.assignee_face

        reporter_face: dict[str, Any] | None | Unset
        if isinstance(self.reporter_face, Unset):
            reporter_face = UNSET
        elif isinstance(self.reporter_face, RelayFace):
            reporter_face = self.reporter_face.to_dict()
        else:
            reporter_face = self.reporter_face

        stale = self.stale

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "assignee": assignee,
                "body": body,
                "channel_id": channel_id,
                "closed_at": closed_at,
                "created_at": created_at,
                "due_at": due_at,
                "id": id,
                "key": key,
                "labels": labels,
                "last_activity_at": last_activity_at,
                "parent_id": parent_id,
                "priority": priority,
                "reporter": reporter,
                "root_message_id": root_message_id,
                "run_id": run_id,
                "state": state,
                "title": title,
                "updated_at": updated_at,
            }
        )
        if assignee_face is not UNSET:
            field_dict["assignee_face"] = assignee_face
        if reporter_face is not UNSET:
            field_dict["reporter_face"] = reporter_face
        if stale is not UNSET:
            field_dict["stale"] = stale

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.relay_face import RelayFace

        d = dict(src_dict)

        def _parse_assignee(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        assignee = _parse_assignee(d.pop("assignee"))

        body = d.pop("body")

        channel_id = d.pop("channel_id")

        def _parse_closed_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        closed_at = _parse_closed_at(d.pop("closed_at"))

        def _parse_created_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        created_at = _parse_created_at(d.pop("created_at"))

        def _parse_due_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        due_at = _parse_due_at(d.pop("due_at"))

        id = d.pop("id")

        key = d.pop("key")

        labels = cast(list[str], d.pop("labels"))

        def _parse_last_activity_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        last_activity_at = _parse_last_activity_at(d.pop("last_activity_at"))

        def _parse_parent_id(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        parent_id = _parse_parent_id(d.pop("parent_id"))

        priority = d.pop("priority")

        reporter = d.pop("reporter")

        def _parse_root_message_id(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        root_message_id = _parse_root_message_id(d.pop("root_message_id"))

        def _parse_run_id(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        run_id = _parse_run_id(d.pop("run_id"))

        state = d.pop("state")

        title = d.pop("title")

        def _parse_updated_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        updated_at = _parse_updated_at(d.pop("updated_at"))

        def _parse_assignee_face(data: object) -> None | RelayFace | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                assignee_face_type_0 = RelayFace.from_dict(data)

                return assignee_face_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | RelayFace | Unset, data)

        assignee_face = _parse_assignee_face(d.pop("assignee_face", UNSET))

        def _parse_reporter_face(data: object) -> None | RelayFace | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                reporter_face_type_0 = RelayFace.from_dict(data)

                return reporter_face_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | RelayFace | Unset, data)

        reporter_face = _parse_reporter_face(d.pop("reporter_face", UNSET))

        stale = d.pop("stale", UNSET)

        ticket_view = cls(
            assignee=assignee,
            body=body,
            channel_id=channel_id,
            closed_at=closed_at,
            created_at=created_at,
            due_at=due_at,
            id=id,
            key=key,
            labels=labels,
            last_activity_at=last_activity_at,
            parent_id=parent_id,
            priority=priority,
            reporter=reporter,
            root_message_id=root_message_id,
            run_id=run_id,
            state=state,
            title=title,
            updated_at=updated_at,
            assignee_face=assignee_face,
            reporter_face=reporter_face,
            stale=stale,
        )

        ticket_view.additional_properties = d
        return ticket_view

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
