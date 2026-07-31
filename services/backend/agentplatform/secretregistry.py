"""Secrets as first-class components (docs/design/10). A secret is a directory
under the repo's `secrets/` tree containing a `secret.yaml` that declares
everything ABOUT the secret except its value: its keys (which become env vars
when a skill binds it), set-time hints, whether the platform requires it, and
how to verify it. Values live only in k8s Secrets, set via the API/UI.

Mirrors skills.SkillStore: folders in the synced git checkout, reloaded on
read, folder name as the default name."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError, model_validator


class SecretKey(BaseModel):
    name: str
    hint: str = ""
    # Informational: "base64", "pem", "url", … — surfaced in hints/docs only.
    format: str = ""


class ProbeSpec(BaseModel):
    """A declarative verification probe: GET `url` with `headers`, 2xx = valid.
    `{key}` placeholders interpolate the secret's data (see secretverify)."""
    url: str
    headers: dict[str, str] = {}


class VerifySpec(BaseModel):
    probe: ProbeSpec | None = None
    # A verify_*.py filename in the secret's folder, run in a sandboxed
    # subprocess with only this secret's data in its env. Exit 0 = valid.
    script: str | None = None
    # Verified by run outcomes (the recorder marks valid/invalid) — no check
    # the platform can run on demand. Claude's token.
    run: bool = False

    @model_validator(mode="after")
    def _exactly_one(self):
        if sum([self.probe is not None, self.script is not None, self.run]) != 1:
            raise ValueError("verify: declare exactly one of probe / script / run")
        return self


class SecretSpec(BaseModel):
    name: str
    description: str = ""
    # Set-time hint when the keys can't express it (flexible-key or multi-key
    # secrets); single-key secrets default to that key's hint.
    hint: str = ""
    # The platform can't operate without it (gates setup, alarms the Dashboard).
    required: bool = False
    keys: list[SecretKey] = []
    verify: VerifySpec | None = None

    @property
    def verifiable(self) -> bool:
        """Has a check the platform itself can run (probe or script)."""
        v = self.verify
        return v is not None and (v.probe is not None or v.script is not None)


class SecretInfo(BaseModel):
    name: str
    spec: SecretSpec | None
    dir: Path
    error: str | None = None


class SecretRegistry:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        self._cache: dict[str, SecretInfo] = {}
        self.reload()

    def reload(self) -> None:
        found: dict[str, SecretInfo] = {}
        if self.root.is_dir():
            for d in sorted(p for p in self.root.iterdir() if p.is_dir()):
                info = self._load(d)
                if info is not None:
                    found[info.name] = info
        self._cache = found

    def _load(self, d: Path) -> SecretInfo | None:
        yml = d / "secret.yaml"
        if not yml.is_file():
            return None
        try:
            raw = yaml.safe_load(yml.read_text()) or {}
            raw.setdefault("name", d.name)
            return SecretInfo(name=raw["name"], spec=SecretSpec(**raw), dir=d)
        except (yaml.YAMLError, ValidationError) as e:
            return SecretInfo(name=d.name, spec=None, dir=d, error=str(e))

    def list(self) -> list[SecretInfo]:
        return list(self._cache.values())

    def get(self, name: str) -> SecretInfo | None:
        return self._cache.get(name)

    def required(self) -> list[str]:
        return [i.name for i in self._cache.values() if i.spec and i.spec.required]
