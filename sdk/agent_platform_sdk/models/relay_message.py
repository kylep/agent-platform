from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.relay_face import RelayFace
    from ..models.relay_message_card_type_0 import RelayMessageCardType0
    from ..models.relay_reaction_view import RelayReactionView


T = TypeVar("T", bound="RelayMessage")


@_attrs_define
class RelayMessage:
    """
    Attributes:
        author (str):
        body (str):
        card (None | RelayMessageCardType0):
        channel_id (str):
        created_at (None | str):
        edited_at (None | str):
        face (None | RelayFace):
        hop (int):
        id (str):
        kind (str):
        mentions (list[str]):
        reply_to (None | str):
        run_id (None | str):
        thread_root (None | str):
        external_message_id (None | str | Unset):
        reactions (list[RelayReactionView] | Unset):
        source_binding_id (None | str | Unset):
    """

    author: str
    body: str
    card: None | RelayMessageCardType0
    channel_id: str
    created_at: None | str
    edited_at: None | str
    face: None | RelayFace
    hop: int
    id: str
    kind: str
    mentions: list[str]
    reply_to: None | str
    run_id: None | str
    thread_root: None | str
    external_message_id: None | str | Unset = UNSET
    reactions: list[RelayReactionView] | Unset = UNSET
    source_binding_id: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.relay_face import RelayFace
        from ..models.relay_message_card_type_0 import RelayMessageCardType0

        author = self.author

        body = self.body

        card: dict[str, Any] | None
        if isinstance(self.card, RelayMessageCardType0):
            card = self.card.to_dict()
        else:
            card = self.card

        channel_id = self.channel_id

        created_at: None | str
        created_at = self.created_at

        edited_at: None | str
        edited_at = self.edited_at

        face: dict[str, Any] | None
        if isinstance(self.face, RelayFace):
            face = self.face.to_dict()
        else:
            face = self.face

        hop = self.hop

        id = self.id

        kind = self.kind

        mentions = self.mentions

        reply_to: None | str
        reply_to = self.reply_to

        run_id: None | str
        run_id = self.run_id

        thread_root: None | str
        thread_root = self.thread_root

        external_message_id: None | str | Unset
        if isinstance(self.external_message_id, Unset):
            external_message_id = UNSET
        else:
            external_message_id = self.external_message_id

        reactions: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.reactions, Unset):
            reactions = []
            for reactions_item_data in self.reactions:
                reactions_item = reactions_item_data.to_dict()
                reactions.append(reactions_item)

        source_binding_id: None | str | Unset
        if isinstance(self.source_binding_id, Unset):
            source_binding_id = UNSET
        else:
            source_binding_id = self.source_binding_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "author": author,
                "body": body,
                "card": card,
                "channel_id": channel_id,
                "created_at": created_at,
                "edited_at": edited_at,
                "face": face,
                "hop": hop,
                "id": id,
                "kind": kind,
                "mentions": mentions,
                "reply_to": reply_to,
                "run_id": run_id,
                "thread_root": thread_root,
            }
        )
        if external_message_id is not UNSET:
            field_dict["external_message_id"] = external_message_id
        if reactions is not UNSET:
            field_dict["reactions"] = reactions
        if source_binding_id is not UNSET:
            field_dict["source_binding_id"] = source_binding_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.relay_face import RelayFace
        from ..models.relay_message_card_type_0 import RelayMessageCardType0
        from ..models.relay_reaction_view import RelayReactionView

        d = dict(src_dict)
        author = d.pop("author")

        body = d.pop("body")

        def _parse_card(data: object) -> None | RelayMessageCardType0:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                card_type_0 = RelayMessageCardType0.from_dict(data)

                return card_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | RelayMessageCardType0, data)

        card = _parse_card(d.pop("card"))

        channel_id = d.pop("channel_id")

        def _parse_created_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        created_at = _parse_created_at(d.pop("created_at"))

        def _parse_edited_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        edited_at = _parse_edited_at(d.pop("edited_at"))

        def _parse_face(data: object) -> None | RelayFace:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                face_type_0 = RelayFace.from_dict(data)

                return face_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | RelayFace, data)

        face = _parse_face(d.pop("face"))

        hop = d.pop("hop")

        id = d.pop("id")

        kind = d.pop("kind")

        mentions = cast(list[str], d.pop("mentions"))

        def _parse_reply_to(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        reply_to = _parse_reply_to(d.pop("reply_to"))

        def _parse_run_id(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        run_id = _parse_run_id(d.pop("run_id"))

        def _parse_thread_root(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        thread_root = _parse_thread_root(d.pop("thread_root"))

        def _parse_external_message_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        external_message_id = _parse_external_message_id(
            d.pop("external_message_id", UNSET)
        )

        _reactions = d.pop("reactions", UNSET)
        reactions: list[RelayReactionView] | Unset = UNSET
        if _reactions is not UNSET:
            reactions = []
            for reactions_item_data in _reactions:
                reactions_item = RelayReactionView.from_dict(reactions_item_data)

                reactions.append(reactions_item)

        def _parse_source_binding_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        source_binding_id = _parse_source_binding_id(d.pop("source_binding_id", UNSET))

        relay_message = cls(
            author=author,
            body=body,
            card=card,
            channel_id=channel_id,
            created_at=created_at,
            edited_at=edited_at,
            face=face,
            hop=hop,
            id=id,
            kind=kind,
            mentions=mentions,
            reply_to=reply_to,
            run_id=run_id,
            thread_root=thread_root,
            external_message_id=external_message_id,
            reactions=reactions,
            source_binding_id=source_binding_id,
        )

        relay_message.additional_properties = d
        return relay_message

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
