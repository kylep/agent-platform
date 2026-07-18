import base64
from kubernetes import client as k8s

REQUIRED_SECRETS = ["claude-credentials"]

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
