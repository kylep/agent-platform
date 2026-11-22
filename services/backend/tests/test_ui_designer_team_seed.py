"""Design team defaults respect existing teams and never post messages."""
from sqlalchemy import create_engine, delete, select

from agentplatform.db import Base, Conversation, Principal, RelayMessage, RelayParticipant, SchemaMark, Team, TeamAgent
from agentplatform.ui_designer.team_seed import ensure_design_team


def test_design_team_has_closed_room_admin_owners_and_no_messages_or_grants():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine, tables=[model.__table__ for model in
        (Conversation, Principal, RelayMessage, RelayParticipant, SchemaMark, Team, TeamAgent)])
    with engine.begin() as conn:
        conn.execute(Principal.__table__.insert().values(name='kyle', role='admin'))
        conn.execute(Principal.__table__.insert().values(name='reader', role='reader'))
        ensure_design_team(conn)
        ensure_design_team(conn)
        team = conn.execute(select(Team.__table__)).mappings().one()
        assert team['slug'] == 'design' and team['name'] == 'Design'
        room = conn.execute(select(Conversation.__table__).where(
            Conversation.id == team['relay_channel_id'])).mappings().one()
        assert room['kind'] == 'group' and room['team_id'] == team['id']
        people = conn.execute(select(RelayParticipant.participant, RelayParticipant.role)).all()
        assert people == [('user:kyle', 'owner')]
        assert not conn.execute(select(TeamAgent.agent)).all()
        assert not conn.execute(select(RelayMessage.id)).all()
        conn.execute(delete(Team).where(Team.id == team['id']))
        ensure_design_team(conn)
        assert not conn.execute(select(Team.id)).all()


def test_existing_design_team_is_adopted_without_changing_members_or_room():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine, tables=[model.__table__ for model in
        (Conversation, Principal, RelayMessage, RelayParticipant, SchemaMark, Team, TeamAgent)])
    with engine.begin() as conn:
        conn.execute(Team.__table__.insert().values(id='a' * 32, slug='design',
            name='My designers', description='Custom', relay_channel_id='b' * 32))
        ensure_design_team(conn)
        team = conn.execute(select(Team.__table__)).mappings().one()
        assert team['name'] == 'My designers' and team['relay_channel_id'] == 'b' * 32
        assert not conn.execute(select(Conversation.id)).all()
        assert not conn.execute(select(RelayParticipant.participant)).all()
