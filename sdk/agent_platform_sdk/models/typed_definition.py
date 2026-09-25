from __future__ import annotations

from collections.abc import Mapping
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    TypeVar,
    cast,
)

from attrs import define as _attrs_define
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.action_binding import ActionBinding
    from ..models.read_binding import ReadBinding
    from ..models.typed_block import TypedBlock


T = TypeVar("T", bound="TypedDefinition")


@_attrs_define
class TypedDefinition:
    """
    Attributes:
        title (str):
        actions (list[ActionBinding] | Unset):
        blocks (list[TypedBlock] | Unset):
        reads (list[ReadBinding] | Unset):
        renderer (Literal['typed/v1'] | Unset):  Default: 'typed/v1'.
    """

    title: str
    actions: list[ActionBinding] | Unset = UNSET
    blocks: list[TypedBlock] | Unset = UNSET
    reads: list[ReadBinding] | Unset = UNSET
    renderer: Literal["typed/v1"] | Unset = "typed/v1"

    def to_dict(self) -> dict[str, Any]:
        title = self.title

        actions: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.actions, Unset):
            actions = []
            for actions_item_data in self.actions:
                actions_item = actions_item_data.to_dict()
                actions.append(actions_item)

        blocks: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.blocks, Unset):
            blocks = []
            for blocks_item_data in self.blocks:
                blocks_item = blocks_item_data.to_dict()
                blocks.append(blocks_item)

        reads: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.reads, Unset):
            reads = []
            for reads_item_data in self.reads:
                reads_item = reads_item_data.to_dict()
                reads.append(reads_item)

        renderer = self.renderer

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "title": title,
            }
        )
        if actions is not UNSET:
            field_dict["actions"] = actions
        if blocks is not UNSET:
            field_dict["blocks"] = blocks
        if reads is not UNSET:
            field_dict["reads"] = reads
        if renderer is not UNSET:
            field_dict["renderer"] = renderer

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.action_binding import ActionBinding
        from ..models.read_binding import ReadBinding
        from ..models.typed_block import TypedBlock

        d = dict(src_dict)
        title = d.pop("title")

        _actions = d.pop("actions", UNSET)
        actions: list[ActionBinding] | Unset = UNSET
        if _actions is not UNSET:
            actions = []
            for actions_item_data in _actions:
                actions_item = ActionBinding.from_dict(actions_item_data)

                actions.append(actions_item)

        _blocks = d.pop("blocks", UNSET)
        blocks: list[TypedBlock] | Unset = UNSET
        if _blocks is not UNSET:
            blocks = []
            for blocks_item_data in _blocks:
                blocks_item = TypedBlock.from_dict(blocks_item_data)

                blocks.append(blocks_item)

        _reads = d.pop("reads", UNSET)
        reads: list[ReadBinding] | Unset = UNSET
        if _reads is not UNSET:
            reads = []
            for reads_item_data in _reads:
                reads_item = ReadBinding.from_dict(reads_item_data)

                reads.append(reads_item)

        renderer = cast(Literal["typed/v1"] | Unset, d.pop("renderer", UNSET))
        if renderer != "typed/v1" and not isinstance(renderer, Unset):
            raise ValueError(f"renderer must match const 'typed/v1', got '{renderer}'")

        typed_definition = cls(
            title=title,
            actions=actions,
            blocks=blocks,
            reads=reads,
            renderer=renderer,
        )

        return typed_definition
