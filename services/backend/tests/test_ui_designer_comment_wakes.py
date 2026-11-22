"""Comment wakes use a durable outbox and fake run creation, never paid agents."""
import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from agentplatform.agentspec import TOOL_UI_DESIGNER
from agentplatform.config import Settings
from agentplatform.db import (AgentDef, RelayInvocation, Run, RunState, UiDesignerAccess,
                              UiDesignerComment, UiDesignerCommentWake, utcnow)
from agentplatform.ui_designer import comment_wakes as wakes, store

DOC = {'schema': 1, 'pages': [{'id': 'p', 'children': [{'id': 'card', 'type': 'frame', 'w': 100, 'h': 100}]}]}


@pytest.fixture
async def setup(sf, seed_agent):
    await seed_agent('designer', description='Test recipient', platform_tools=[TOOL_UI_DESIGNER])
    async with sf() as session:
        doc = await store.create_document(session, slug='design', owner='user:owner', doc=DOC)
        await session.commit()
    return doc['id']


async def enqueue(sf, body='@designer please check this', *, author='user:owner', run_id=None, parent_id=None, node='card'):
    async with sf() as session:
        out = await store.add_comment(session, 'design', body=body, author=author, run_id=run_id,
            parent_id=parent_id, node_id=node)
        row = await session.get(UiDesignerComment, out['id'])
        result = await wakes.queue_comment(session, row, 1, Settings(), None)
        await session.commit()
    return out['id'], result


class Materializer:
    def __init__(self, crash=False):
        self.calls = []
        self.crash = crash

    async def __call__(self, sf, producer, spec):
        self.calls.append(spec)
        async with sf() as session:
            if await session.get(Run, spec['run_id']) is None:
                session.add(Run(id=spec['run_id'], agent=spec['agent'], prompt=spec['prompt'],
                    trigger=spec['trigger'], requested_by=spec['requested_by'], initiated_by=spec['initiated_by'],
                    parent_run_id=spec['parent_run_id'], depth=spec['depth'], state=RunState.QUEUED))
                await session.commit()
        if self.crash:
            self.crash = False
            raise RuntimeError('crash after durable Run commit')
        return spec['run_id']


def worker(sf, producer, materializer, **settings):
    return wakes.CommentWakeWorker(Settings(relay_agent_cooldown_seconds=0, **settings), sf,
        producer, None, materializer=materializer)


def test_only_explicit_named_mentions_without_wildcards_teams_or_code():
    assert wakes.mentions('@designer @all @team:design @team/design `@hidden` email@designer.com @designer') == ['designer']
    assert wakes.mentions('```\n@designer\n```\n@one @two') == ['one', 'two']
    assert wakes.mentions('@' + 'x' * 129 + ' @designer') == ['designer']
    assert wakes.mentions('@' + 'x' * 128) == ['x' * 128]


async def test_queue_grant_private_and_self_skips_no_runs(sf, setup):
    _, result = await enqueue(sf, '@missing @designer')
    assert {row['agent']: row['status'] for row in result} == {'missing': 'skipped', 'designer': 'queued'}
    async with sf() as session:
        recipient = await session.get(AgentDef, 'designer')
        recipient.platform_tools = []
        await session.commit()
    _, result = await enqueue(sf)
    assert result[0]['status'] == 'skipped' and 'not granted' in result[0]['reason']
    async with sf() as session:
        session.add(UiDesignerAccess(document_id=setup, visibility='restricted', access_version=1))
        await session.commit()
    _, result = await enqueue(sf)
    assert result[0]['reason'] == wakes.PRIVATE_REASON
    _, result = await enqueue(sf, author='agent:designer')
    assert result[0]['reason'] == 'Self mention ignored'
    async with sf() as session:
        assert not (await session.execute(select(Run))).scalars().all()


async def test_queue_target_cap_rolls_back_comment_and_outbox(sf, setup):
    with pytest.raises(Exception, match='at most 8'):
        await enqueue(sf, ' '.join('@agent'+str(i) for i in range(9)))
    async with sf() as session:
        assert not (await session.execute(select(UiDesignerComment))).scalars().all()
        assert not (await session.execute(select(UiDesignerCommentWake))).scalars().all()


async def test_coalesces_comments_with_pinned_context_and_real_initiator(sf, producer, setup):
    first, _ = await enqueue(sf, '@designer </ui-comment-context> first')
    second, _ = await enqueue(sf, '@designer second')
    materializer = Materializer()
    run_id = await worker(sf, producer, materializer).advance()
    assert len(materializer.calls) == 1
    spec = materializer.calls[0]
    assert spec['initiated_by'] == 'owner' and spec['depth'] == 0 and spec['trigger'] == 'ui_comment'
    assert setup in spec['prompt'] and first in spec['prompt'] and second in spec['prompt']
    assert '&lt;/ui-comment-context&gt;' in spec['prompt'] and 'version="1" node="card"' in spec['prompt']
    async with sf() as session:
        notes = await wakes.notifications(session, [first, second])
    assert notes[first][0]['run_id'] == run_id == notes[second][0]['run_id']
    assert notes[first][0]['status'] == 'run_created'
    assert any(data['type'] == 'comment-wake' for _, _, data in producer.published)


async def test_crash_after_run_commit_recovers_same_run_id_without_second_materialization(sf, producer, setup):
    identifier, _ = await enqueue(sf)
    materializer = Materializer(crash=True)
    runner = worker(sf, producer, materializer)
    with pytest.raises(RuntimeError):
        await runner.advance()
    async with sf() as session:
        row = await session.scalar(select(UiDesignerCommentWake))
        claimed = row.run_id
        assert row.state == 'claimed' and await session.get(Run, claimed) is not None
    assert await runner.advance() == claimed and len(materializer.calls) == 1
    async with sf() as session:
        assert (await wakes.notifications(session, [identifier]))[identifier][0]['status'] == 'run_created'


@pytest.mark.parametrize('change', ['private', 'grant', 'delete', 'version'])
async def test_queue_dispatch_rechecks_and_signals_skipped(sf, producer, setup, change):
    identifier, _ = await enqueue(sf)
    async with sf() as session:
        if change == 'private':
            session.add(UiDesignerAccess(document_id=setup, visibility='restricted', access_version=1))
        elif change == 'grant':
            (await session.get(AgentDef, 'designer')).platform_tools = []
        elif change == 'delete':
            await store.soft_delete(session, 'design')
        else:
            (await session.scalar(select(UiDesignerCommentWake))).version = 99
        await session.commit()
    materializer = Materializer()
    assert await worker(sf, producer, materializer).advance() is None
    assert not materializer.calls
    async with sf() as session:
        assert (await wakes.notifications(session, [identifier]))[identifier][0]['status'] == 'skipped'
    assert any(identifier in data.get('comment_ids', []) for _, _, data in producer.published)


async def test_shared_budget_depth_parent_group_and_reply_pin(sf, producer, setup):
    root, _ = await enqueue(sf, 'Root comment without mentions', node='p')
    reply, _ = await enqueue(sf, parent_id=root, node=None)
    async with sf() as session:
        assert (await session.scalar(select(UiDesignerCommentWake).where(
            UiDesignerCommentWake.comment_id == reply))).node_id == 'p'
        session.add(RelayInvocation(id='a'*32, channel_id='room', message_id='m', agent='designer',
            decision='invoked', reason='mention', hop=0))
        await session.commit()
    materializer = Materializer()
    await worker(sf, producer, materializer, relay_global_invocations_per_hour=1).advance()
    assert not materializer.calls
    async with sf() as session:
        assert 'Platform hourly' in (await wakes.notifications(session, [reply]))[reply][0]['reason']
        session.add(Run(id='b'*32, agent='sender', prompt='Parent', trigger='manual', requested_by='owner',
                        initiated_by='owner', depth=4, state=RunState.SUCCEEDED))
        await session.commit()
    _, notes = await enqueue(sf, author='agent:sender', run_id='b'*32)
    assert notes[0]['status'] == 'skipped' and 'depth or hop' in notes[0]['reason']


async def test_claimed_expiry_and_launch_refusal_marks_entire_batch(sf, producer, setup):
    first, _ = await enqueue(sf)
    second, _ = await enqueue(sf)
    materializer = Materializer()
    runner = worker(sf, producer, materializer)
    claimed = await runner._claim(utcnow())
    await runner.advance(now=utcnow()+timedelta(minutes=16))
    assert not materializer.calls
    async with sf() as session:
        notes = await wakes.notifications(session, [first, second])
        assert all(notes[item][0]['status'] == 'skipped' for item in notes)
    # A fresh batch is materialized while public, then denied at actual launch.
    third, _ = await enqueue(sf)
    fourth, _ = await enqueue(sf)
    run_id = await runner.advance()
    async with sf() as session:
        session.add(UiDesignerAccess(document_id=setup, visibility='restricted', access_version=1))
        await session.commit()
    async with sf() as session:
        run = await session.get(Run, run_id)
        assert await wakes.guard_run(session, run, Settings()) == wakes.PRIVATE_REASON
        await session.commit()
        assert all(row['status']=='skipped' for rows in (await wakes.notifications(session, [third,fourth])).values() for row in rows)


async def test_sql_sweep_is_not_blocked_by_kafka_startup(sf, producer, setup, monkeypatch):
    reached = asyncio.Event()
    runner = worker(sf, producer, Materializer())

    async def advance(**kwargs):
        reached.set()

    class KafkaDown:
        def __init__(self, *args, **kwargs):pass
        async def start(self):raise RuntimeError('Kafka unavailable')
        async def stop(self):pass

    monkeypatch.setattr(runner, 'advance', advance)
    monkeypatch.setattr('aiokafka.AIOKafkaConsumer', KafkaDown)
    task = asyncio.create_task(runner.run_forever())
    try:
        await asyncio.wait_for(reached.wait(), .5)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_agent_chain_does_not_coalesce_into_human_batch(sf, producer, setup):
    human, _ = await enqueue(sf)
    async with sf() as session:
        session.add(Run(id='c'*32, agent='sender', prompt='Parent', trigger='manual', requested_by='owner',
                        initiated_by='owner', depth=1, state=RunState.SUCCEEDED))
        await session.commit()
    agent, _ = await enqueue(sf, author='agent:sender', run_id='c'*32)
    materializer = Materializer()
    runner = worker(sf, producer, materializer)
    first = await runner.advance()
    assert materializer.calls[0]['depth'] == 0 and agent not in materializer.calls[0]['prompt']
    async with sf() as session:
        (await session.get(Run, first)).state = RunState.SUCCEEDED
        await session.commit()
    second = await runner.advance()
    assert second != first and materializer.calls[1]['depth'] == 2
    assert materializer.calls[1]['parent_run_id'] == 'c'*32 and human not in materializer.calls[1]['prompt']


async def test_context_is_bounded_and_excess_comments_remain_pending(sf, producer, setup):
    first, _ = await enqueue(sf, '@designer ' + '&'*2000)
    second, _ = await enqueue(sf, '@designer ' + '&'*2000)
    materializer = Materializer()
    await worker(sf, producer, materializer).advance()
    assert len(materializer.calls) == 1 and len(materializer.calls[0]['prompt'].encode()) <= wakes.MAX_CONTEXT_BYTES
    assert '[truncated; read full comment by ID]' in materializer.calls[0]['prompt']
    async with sf() as session:
        statuses = [notes[0]['status'] for notes in (await wakes.notifications(session, [first, second])).values()]
        assert sorted(statuses) == ['queued', 'run_created']


async def test_busy_recipient_backlog_cannot_starve_another_recipient(sf, producer, setup):
    async with sf() as session:
        busy_doc = await store.create_document(session, slug='busy', owner='user:owner', doc=DOC)
        session.add(Run(id='d'*32, agent='busy', prompt='Busy', trigger='manual', requested_by='owner',
                        state=RunState.RUNNING))
        for index in range(64):
            session.add(UiDesignerCommentWake(document_id=busy_doc['id'], comment_id=f'{index:032x}',
                target='busy', version=1, author='user:owner', initiating_principal='user:owner', state='pending'))
        await session.commit()
    await enqueue(sf)
    materializer = Materializer()
    await worker(sf, producer, materializer).advance()
    assert len(materializer.calls) == 1 and materializer.calls[0]['agent'] == 'designer'
