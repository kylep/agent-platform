import asyncio, json, os, shutil, subprocess, sys
from pathlib import Path
from aiokafka import AIOKafkaProducer

TOPIC_TRANSCRIPT, TOPIC_EVENTS = "run.transcript", "run.events"

class KafkaProducerWrapper:
    def __init__(self, bootstrap): self._p = AIOKafkaProducer(bootstrap_servers=bootstrap)
    async def start(self): await self._p.start()
    async def stop(self): await self._p.stop()
    async def publish(self, topic, key, value):
        await self._p.send_and_wait(topic, json.dumps(value).encode(), key=key.encode())

def _install_credentials() -> None:
    src = Path(os.environ.get("AP_SECRETS_DIR", "/secrets/claude")) / "credentials.json"
    dst = Path.home() / ".claude" / ".credentials.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, dst)  # copy: never write back to the mount

def run(producer=None) -> int:
    run_id, agent = os.environ["AP_RUN_ID"], os.environ["AP_AGENT"]
    prompt = os.environ["AP_PROMPT"]
    producer = producer or KafkaProducerWrapper(os.environ.get("AP_KAFKA_BOOTSTRAP", "kafka:9092"))
    return asyncio.run(_run(producer, run_id, agent, prompt))

async def _run(producer, run_id: str, agent: str, prompt: str) -> int:
    _install_credentials()
    await producer.start()
    claude = os.environ.get("CLAUDE_BIN", "claude")
    proc = subprocess.Popen(
        [claude, "--agent", agent, "-p", prompt, "--output-format", "stream-json", "--verbose"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
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
