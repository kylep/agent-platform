from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from typing_extensions import Self

T = TypeVar("T", bound="WebhookSecretIn")


@_attrs_define
class WebhookSecretIn:
    """Setting/rotating one webhook's shared secret (docs/design/16). Its own
    endpoint rather than a definition field, because the value must never take
    the definition's path through `agent_versions`. Write-only: no response
    model carries it back, and nothing reads it out again.

        Attributes:
            secret (str):
    """

    secret: str

    def to_dict(self) -> dict[str, Any]:
        secret = self.secret

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "secret": secret,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        secret = d.pop("secret")

        webhook_secret_in = cls(
            secret=secret,
        )

        return webhook_secret_in
