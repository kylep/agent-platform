"""One-time Design team seed, serialized by init_db's migration lock."""
import uuid

from sqlalchemy import select

MARK = 'ui-designer-design-team-v1'


def ensure_design_team(conn):
    from agentplatform.db import Conversation, Principal, RelayParticipant, SchemaMark, Team, utcnow

    marks = SchemaMark.__table__
    if conn.execute(select(marks.c.name).where(marks.c.name == MARK)).first():
        return
    teams = Team.__table__
    if not conn.execute(select(teams.c.id).where(teams.c.slug == 'design')).first():
        team_id, room_id = uuid.uuid4().hex, uuid.uuid4().hex
        conn.execute(teams.insert().values(id=team_id, slug='design', name='Design',
            description='People and agents collaborating on UI Designer documents.',
            relay_channel_id=room_id))
        # Team rooms are closed groups. The existing public #design showcase
        # keeps its identity and visibility; no message is posted by this seed.
        conn.execute(Conversation.__table__.insert().values(id=room_id, connector='web',
            agent=None, kind='group', home='relay', reply_mode='linear',
            dispatch_mode='mentions', title='Design', team_id=team_id))
        principals = Principal.__table__
        admins = list(conn.execute(select(principals.c.name).where(
            principals.c.role == 'admin')).scalars()) or ['admin']
        for name in admins:
            conn.execute(RelayParticipant.__table__.insert().values(channel_id=room_id,
                participant=f'user:{name}', role='owner'))
    conn.execute(marks.insert().values(name=MARK, applied_at=utcnow()))
