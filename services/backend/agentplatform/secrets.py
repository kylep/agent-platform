import base64
from kubernetes import client as k8s

CLAUDE_CREDENTIAL = "claude-credentials"
REQUIRED_SECRETS = [CLAUDE_CREDENTIAL]

# Format hints for the platform's known secrets. `key` is the suggested data key
# (blank → let the UI heuristic decide). This matters for correctness, not just
# UX: a skill's secret is bound via envFrom, so its *key* becomes the env var the
# skill reads (e.g. GITHUB_TOKEN). `hint` describes the value.
SECRET_HINTS: dict[str, dict[str, str]] = {
    "claude-credentials": {"key": "",
        "hint": "A `claude setup-token` value, or paste credentials.json contents (JSON is auto-detected)."},
    "discord-bot": {"key": "token",
        "hint": "Discord bot token — Developer Portal → your app → Bot → Reset/Copy Token."},
    "discord-webhook": {"key": "DISCORD_WEBHOOK_URL",
        "hint": "Discord incoming webhook URL, e.g. https://discord.com/api/webhooks/…"},
    "github-token": {"key": "GITHUB_TOKEN",
        "hint": "GitHub token/PAT with repo scope — skills read it as $GITHUB_TOKEN."},
    "github-app": {"key": "",
        "hint": "GitHub App creds (app_id, install_id, private_key) — a multi-key secret, set via the API."},
    "github-deploy-key": {"key": "id_ed25519",
        "hint": "An SSH private deploy key with push access."},
}

# Secrets that can be validated with a cheap read-only API call. Returns the
# (url, headers) to GET; a 2xx means the credential authenticates. (claude-
# credentials is validated differently — via a run's success, in the recorder.)
def secret_probe_target(name: str, data: dict[str, str]) -> tuple[str, dict[str, str]] | None:
    # Discord's API is behind Cloudflare, which 403s requests lacking a real
    # User-Agent (urllib's default is blocked) — so every Discord probe sets one.
    _UA = "DiscordBot (https://github.com/kylep/agent-platform, 1.0)"
    if name == "discord-bot":
        tok = data.get("token", "").strip()
        # Tolerate a pasted "Bot " prefix (the header adds its own).
        if tok.lower().startswith("bot "):
            tok = tok[4:].strip()
        return "https://discord.com/api/v10/users/@me", {"Authorization": f"Bot {tok}", "User-Agent": _UA}
    if name == "github-token":
        tok = data.get("GITHUB_TOKEN") or data.get("token", "")
        return "https://api.github.com/user", {"Authorization": f"Bearer {tok}", "User-Agent": "agent-platform"}
    if name == "discord-webhook":
        url = data.get("DISCORD_WEBHOOK_URL") or data.get("token", "")
        return (url, {"User-Agent": _UA}) if url.startswith("http") else None
    return None


PROBEABLE_SECRETS = {"discord-bot", "github-token", "discord-webhook"}


class SecretStore:
    async def set(self, name: str, data: dict[str, str]) -> None: raise NotImplementedError
    async def get(self, name: str) -> dict[str, str] | None: raise NotImplementedError
    async def exists(self, name: str) -> bool:
        return await self.get(name) is not None

class InMemorySecretStore(SecretStore):
    def __init__(self): self._d: dict[str, dict[str, str]] = {}
    async def set(self, name, data): self._d[name] = dict(data)
    async def get(self, name): return self._d.get(name)

class K8sSecretStore(SecretStore):
    def __init__(self, core: k8s.CoreV1Api, namespace: str):
        self._core, self._ns = core, namespace
    async def set(self, name, data):
        body = k8s.V1Secret(metadata=k8s.V1ObjectMeta(name=name),
                            string_data=data, type="Opaque")
        try:
            self._core.replace_namespaced_secret(name, self._ns, body)
        except k8s.exceptions.ApiException as e:
            if e.status != 404: raise
            self._core.create_namespaced_secret(self._ns, body)
    async def get(self, name):
        try:
            sec = self._core.read_namespaced_secret(name, self._ns)
        except k8s.exceptions.ApiException as e:
            if e.status == 404: return None
            raise
        return {k: base64.b64decode(v).decode() for k, v in (sec.data or {}).items()}
