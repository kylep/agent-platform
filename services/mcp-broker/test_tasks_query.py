"""The tool preserves an existing query unless explicitly changed or cleared."""
import asyncio
from test_relay_tool import broker, caller  # noqa: F401


def test_task_query_schedule_reschedule_and_clear(monkeypatch):
    calls=[]
    async def call(method,path,params=None,json=None,**kw):
        calls.append((method,path,json));return '{}'
    monkeypatch.setattr(broker,'_call',call)
    fn=broker.tasks.__wrapped__
    asyncio.run(fn(action='schedule',agent='one',prompt='execute',delay_minutes=2,memory_query='archive badge'))
    assert calls[-1][2]['memory_query']=='archive badge' and calls[-1][2]['prompt']=='execute'
    tid='a'*32
    asyncio.run(fn(action='reschedule',task_id=tid,version=2,run_at='2026-10-21T13:00:00Z'))
    assert 'memory_query' not in calls[-1][2]
    asyncio.run(fn(action='reschedule',task_id=tid,version=3,run_at='2026-10-21T13:00:00Z',clear_memory_query=True))
    assert calls[-1][2]['memory_query'] is None
    count=len(calls)
    result=asyncio.run(fn(action='reschedule',task_id=tid,version=4,run_at='2026-10-21T13:00:00Z',memory_query='query',clear_memory_query=True))
    assert result.startswith('error:') and len(calls)==count
