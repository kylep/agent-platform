from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.relay_binding_view import RelayBindingView
    from ..models.relay_channel_detail_display_names import (
        RelayChannelDetailDisplayNames,
    )
    from ..models.relay_channel_detail_faces import RelayChannelDetailFaces
    from ..models.relay_last_message import RelayLastMessage


T = TypeVar("T", bound="RelayChannelDetail")


@_attrs_define
class RelayChannelDetail:
    """
    Attributes:
        agent (None | str):
        archived_at (None | str):
        default_agent (None | str):
        dispatch_mode (str):
        faces (RelayChannelDetailFaces):
        home (str):
        id (str):
        kind (str):
        last_message (None | RelayLastMessage):
        message_count (int):
        name (None | str):
        open_ (bool):
        participants (list[str]):
        reply_mode (str):
        ticket_prefix (None | str):
        title (None | str):
        topic (str):
        unread (int):
        bindings (list[RelayBindingView] | Unset):
        display_names (RelayChannelDetailDisplayNames | Unset):
    """

    agent: None | str
    archived_at: None | str
    default_agent: None | str
    dispatch_mode: str
    faces: RelayChannelDetailFaces
    home: str
    id: str
    kind: str
    last_message: None | RelayLastMessage
    message_count: int
    name: None | str
    open_: bool
    participants: list[str]
    reply_mode: str
    ticket_prefix: None | str
    title: None | str
    topic: str
    unread: int
    bindings: list[RelayBindingView] | Unset = UNSET
    display_names: RelayChannelDetailDisplayNames | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.relay_last_message import RelayLastMessage

        agent: None | str
        agent = self.agent

        archived_at: None | str
        archived_at = self.archived_at

        default_agent: None | str
        default_agent = self.default_agent

        dispatch_mode = self.dispatch_mode

        faces = self.faces.to_dict()

        home = self.home

        id = self.id

        kind = self.kind

        last_message: dict[str, Any] | None
        if isinstance(self.last_message, RelayLastMessage):
            last_message = self.last_message.to_dict()
        else:
            last_message = self.last_message

        message_count = self.message_count

        name: None | str
        name = self.name

        open_ = self.open_

        participants = self.participants

        reply_mode = self.reply_mode

        ticket_prefix: None | str
        ticket_prefix = self.ticket_prefix

        title: None | str
        title = self.title

        topic = self.topic

        unread = self.unread

        bindings: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.bindings, Unset):
            bindings = []
            for bindings_item_data in self.bindings:
                bindings_item = bindings_item_data.to_dict()
                bindings.append(bindings_item)

        display_names: dict[str, Any] | Unset = UNSET
        if not isinstance(self.display_names, Unset):
            display_names = self.display_names.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "agent": agent,
                "archived_at": archived_at,
                "default_agent": default_agent,
                "dispatch_mode": dispatch_mode,
                "faces": faces,
                "home": home,
                "id": id,
                "kind": kind,
                "last_message": last_message,
                "message_count": message_count,
                "name": name,
                "open": open_,
                "participants": participants,
                "reply_mode": reply_mode,
                "ticket_prefix": ticket_prefix,
                "title": title,
                "topic": topic,
                "unread": unread,
            }
        )
        if bindings is not UNSET:
            field_dict["bindings"] = bindings
        if display_names is not UNSET:
            field_dict["display_names"] = display_names

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.relay_binding_view import RelayBindingView
        from ..models.relay_channel_detail_display_names import (
            RelayChannelDetailDisplayNames,
        )
        from ..models.relay_channel_detail_faces import RelayChannelDetailFaces
        from ..models.relay_last_message import RelayLastMessage

        d = dict(src_dict)

        def _parse_agent(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        agent = _parse_agent(d.pop("agent"))

        def _parse_archived_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        archived_at = _parse_archived_at(d.pop("archived_at"))

        def _parse_default_agent(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        default_agent = _parse_default_agent(d.pop("default_agent"))

        dispatch_mode = d.pop("dispatch_mode")

        faces = RelayChannelDetailFaces.from_dict(d.pop("faces"))

        home = d.pop("home")

        id = d.pop("id")

        kind = d.pop("kind")

        def _parse_last_message(data: object) -> None | RelayLastMessage:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                last_message_type_0 = RelayLastMessage.from_dict(data)

                return last_message_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | RelayLastMessage, data)

        last_message = _parse_last_message(d.pop("last_message"))

        message_count = d.pop("message_count")

        def _parse_name(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        name = _parse_name(d.pop("name"))

        open_ = d.pop("open")

        participants = cast(list[str], d.pop("participants"))

        reply_mode = d.pop("reply_mode")

        def _parse_ticket_prefix(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        ticket_prefix = _parse_ticket_prefix(d.pop("ticket_prefix"))

        def _parse_title(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        title = _parse_title(d.pop("title"))

        topic = d.pop("topic")

        unread = d.pop("unread")

        _bindings = d.pop("bindings", UNSET)
        bindings: list[RelayBindingView] | Unset = UNSET
        if _bindings is not UNSET:
            bindings = []
            for bindings_item_data in _bindings:
                bindings_item = RelayBindingView.from_dict(bindings_item_data)

                bindings.append(bindings_item)

        _display_names = d.pop("display_names", UNSET)
        display_names: RelayChannelDetailDisplayNames | Unset
        if isinstance(_display_names, Unset):
            display_names = UNSET
        else:
            display_names = RelayChannelDetailDisplayNames.from_dict(_display_names)

        relay_channel_detail = cls(
            agent=agent,
            archived_at=archived_at,
            default_agent=default_agent,
            dispatch_mode=dispatch_mode,
            faces=faces,
            home=home,
            id=id,
            kind=kind,
            last_message=last_message,
            message_count=message_count,
            name=name,
            open_=open_,
            participants=participants,
            reply_mode=reply_mode,
            ticket_prefix=ticket_prefix,
            title=title,
            topic=topic,
            unread=unread,
            bindings=bindings,
            display_names=display_names,
        )

        relay_channel_detail.additional_properties = d
        return relay_channel_detail

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
