import asyncio, json, os, shutil, subprocess, sys
from pathlib import Path
from aiokafka import AIOKafkaProducer

TOPIC_TRANSCRIPT, TOPIC_EVENTS = "run.transcript", "run.events"

class KafkaProducerWrapper:
    # AIOKafkaProducer must be constructed inside a running event loop, so
    # construction is deferred to start() (run() calls us from sync code).
    def __init__(self, bootstrap):
        self._bootstrap = bootstrap
        self._p = None
    async def start(self):
        self._p = AIOKafkaProducer(bootstrap_servers=self._bootstrap)
        await self._p.start()
    async def stop(self): await self._p.stop()
    async def publish(self, topic, key, value):
        await self._p.send_and_wait(topic, json.dumps(value).encode(), key=key.encode())

def _install_credentials() -> dict:
    """Returns extra env for the claude subprocess. Preferred: a long-lived
    `claude setup-token` under the secret's `token` key (nothing rotates it).
    Fallback: a session credentials.json snapshot (goes stale fast — the
    laptop's own claude rotates the refresh token; kept for completeness)."""
    secrets = Path(os.environ.get("AP_SECRETS_DIR", "/secrets/claude"))
    token_file = secrets / "token"
    if token_file.is_file():
        return {"CLAUDE_CODE_OAUTH_TOKEN": token_file.read_text().strip()}
    src = secrets / "credentials.json"
    dst = Path.home() / ".claude" / ".credentials.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, dst)  # copy: never write back to the mount
    return {}

def _install_agent(agent: str) -> None:
    # `claude --agent <name>` resolves agents from ~/.claude/agents/, so the
    # synced definition is copied there under the agent's name.
    src = Path(os.environ.get("AP_AGENTS_DIR", "/agents/agents")) / agent / "agent.md"
    dst = Path.home() / ".claude" / "agents" / f"{agent}.md"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, dst)

def run(producer=None) -> int:
    run_id, agent = os.environ["AP_RUN_ID"], os.environ["AP_AGENT"]
    prompt = os.environ["AP_PROMPT"]
    producer = producer or KafkaProducerWrapper(os.environ.get("AP_KAFKA_BOOTSTRAP", "kafka:9092"))
    return asyncio.run(_run(producer, run_id, agent, prompt))

async def _run(producer, run_id: str, agent: str, prompt: str) -> int:
    extra_env = _install_credentials()
    _install_agent(agent)
    await producer.start()
    claude = os.environ.get("CLAUDE_BIN", "claude")
    proc = subprocess.Popen(
        [claude, "--agent", agent, "-p", prompt, "--output-format", "stream-json", "--verbose"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env={**os.environ, **extra_env})
    seq = 0
    while True:
        line = await asyncio.to_thread(proc.stdout.readline)
        if line == "":
            break
        line = line.strip()
        if not line: continue
        seq += 1
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            payload = {"type": "raw", "text": line}
        payload["seq"] = seq
        await producer.publish(TOPIC_TRANSCRIPT, run_id, payload)
    rc = await asyncio.to_thread(proc.wait)
    state = "succeeded" if rc == 0 else "failed"
    await producer.publish(TOPIC_TRANSCRIPT, run_id,
                           {"seq": seq + 1, "type": "lifecycle", "terminal": True, "state": state})
    await producer.publish(TOPIC_EVENTS, run_id,
                           {"run_id": run_id, "type": "state", "state": state,
                            "exit_code": rc, "terminal": True})
    await producer.stop()
    return rc

if __name__ == "__main__":
    sys.exit(run())
