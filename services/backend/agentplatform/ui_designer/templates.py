"""Small immutable, assets-free starter scenes. Revisioned data, not DB seeds."""
from __future__ import annotations
import copy
from .errors import UiDesignerError
from .schema import normalize_document


def t(id,text,size=14,color='ink',weight=400,**props):
    style={'family':props.pop('family','Inter'),'size':size,'weight':weight,'line':1.3}
    return {'id':id,'type':'text','text':text,'color':'$color.'+color,'style':style,**props}


def f(id,children=(),fill=None,**props):
    out={'id':id,'type':'frame','children':list(children),**props}
    if fill: out['fill']='$color.'+fill
    return out


def row(id,children=(),**props):
    return f(id,children,layout={'dir':'row','gap':props.pop('gap',12),'align':'center',**props.pop('layout',{})},**props)


def col(id,children=(),**props):
    return f(id,children,layout={'dir':'column','gap':props.pop('gap',12),**props.pop('layout',{})},**props)


def shape(id,w,h,fill='accent',kind='rect',**props):
    return {'id':id,'type':kind,'w':w,'h':h,'fill':'$color.'+fill,**props}


def palette(**extra):
    colors={'bg':('#f3f6f8','#0d1822'),'panel':('#ffffff','#172731'),'ink':('#17313d','#f0f6f8'),
        'muted':('#506773','#aec0c9'),'line':('#d8e3e8','#34505e'),'accent':('#176c58','#71d7b6'),
        'wash':('#e0f2ec','#234b43'),'success':('#246e44','#8be4ac'),'onaccent':('#ffffff','#102d25')}
    colors.update(extra)
    return {key:{'light':light,'dark':dark} for key,(light,dark) in colors.items()}


def doc(title,width,height,children,colors=None,components=None,dark=False):
    return normalize_document({'schema':1,'title':title,'modes':['dark','light'] if dark else ['light','dark'],
        'tokens':{'color':colors or palette(),'space':{'xs':4,'s':8,'m':16,'l':24},'radius':{'card':16},
                  'type':{'body':{'family':'Inter','size':14,'weight':400,'line':1.3}}},
        'components':components or {},'pages':[{'id':'page','title':title,'children':[
            f('canvas',children,'bg',w=width,h=height,clip=True)]}]})


def desk_buddy():
    colors=palette(bg=('#f8f4eb','#18221e'),panel=('#fffdf8','#26362e'),accent=('#edb44f','#edb44f'),
                   wash=('#f4ead2','#354535'),ink=('#25392e','#f5f2e8'),muted=('#596855','#bdc9b7'),
                   onaccent=('#25392e','#25392e'))
    chip=col('action-chip',[t('action-label','Focus 25m',12,weight=600,w='fill',align='center')],
             fill='panel',w=136,h=44,radius=12,layout={'justify':'center','pad':8})
    header=row('buddy-header',[t('buddy-title','Desk Buddy',23,weight=500,family='Fredoka'),
        row('buddy-status',[shape('online-dot',7,7,'success',kind='ellipse'),t('online-text','Here for you',11,'muted')],gap=6)],
        x=20,y=16,w=440,h=36,layout={'justify':'space-between'})
    scene=f('buddy-scene',[
        t('scene-kicker','A LITTLE SPACE TO THINK',10,'muted',600,x=20,y=16),
        col('focus-note',[t('focus-note-label','FOCUS',9,'muted',600),t('focus-clock','25:00',21,weight=600,family='JetBrains Mono')],
            fill='panel',x=16,y=48,w=94,h=66,radius=14,layout={'pad':12},gap=2),
        col('energy-note',[t('energy-label','ENERGY',9,'muted',600),t('energy-value','Looking up',12,weight=600)],
            fill='panel',x=320,y=48,w=104,h=66,radius=14,layout={'pad':12},gap=6),
        shape('buddy-body',88,84,'accent',kind='ellipse',x=176,y=38),
        shape('buddy-leaf',26,12,'success',kind='ellipse',x=220,y=26,rotate=-25),
        shape('left-eye',7,10,'ink',kind='ellipse',x=197,y=66),shape('right-eye',7,10,'ink',kind='ellipse',x=229,y=66),
        {'id':'buddy-smile','type':'path','x':210,'y':88,'w':18,'h':10,'d':'M 0 0 Q 9 10 18 0','stroke':{'color':'$color.ink','width':3,'cap':'round'}},
        col('buddy-speech',[t('buddy-phrase','Ready when you are.',16,weight=600,w='fill',align='center'),
            t('buddy-encouragement','One small thing. Then the next.',11,'muted',w='fill',align='center')],
            fill='panel',x=80,y=132,w=280,h=56,radius=16,layout={'pad':8},gap=4)],
        'wash',x=20,y=64,w=440,h=196,radius=20)
    actions=row('buddy-actions',[{'id':'focus','type':'instance','of':'action-chip','w':'fill'},
        {'id':'stretch','type':'instance','of':'action-chip','w':'fill','overrides':{'action-label.text':'Take a stretch'}},
        {'id':'wrap-up','type':'instance','of':'action-chip','w':'fill','overrides':{'action-label.text':'Wrap up'}}],
        x=20,y=272,w=440,h=44,gap=8)
    return doc('Desk Buddy',480,320,[header,scene,actions],colors,{'action-chip':chip})


def phone():
    colors=palette(accent=('#2459cb','#8cb4ff'),wash=('#e6edff','#293e60'),onaccent=('#ffffff','#132b51'))
    metric=col('metric-tile',[t('metric-label','STREAK',10,'muted',600),t('metric-value','7 days',22,weight=700)],
        fill='panel',w=169,h=74,radius=16,layout={'pad':12},gap=4)
    tasks=[]
    for i,(title,detail) in enumerate([('Shape the next idea','Design · 30 min'),('Make room to move','Wellbeing · 10 min'),('Close the open loops','Planning · 15 min')]):
        tasks.append(row(f'task-{i}',[shape(f'task-check-{i}',18,18,'wash',radius=6),
            col(f'task-copy-{i}',[t(f'task-title-{i}',title,13,weight=600),t(f'task-detail-{i}',detail,10,'muted')],gap=3)],
            fill='panel',w='fill',h=50,radius=12,layout={'pad':[8,12]},gap=12))
    nav=[]
    for i,label in enumerate(['Today','Projects','You']):
        nav.append(col(f'nav-{i}',[shape(f'nav-icon-{i}',15,15,'accent' if i==0 else 'muted',kind='ellipse'),
            t(f'nav-label-{i}',label,10,'accent' if i==0 else 'muted',600,w='fill',align='center')],w='fill',gap=5,layout={'align':'center'}))
    content=col('phone-content',[
        row('phone-status',[t('phone-time','9:41',13,weight=700),t('phone-network','LTE  ·  100%',10,'muted')],w='fill',h=24,layout={'justify':'space-between'}),
        col('phone-heading',[t('phone-greeting','GOOD MORNING, ALEX',10,'muted',600),
            t('phone-title','Make time for\nwhat matters.',26,weight=700,w='fill')],w='fill',h=78,gap=5),
        col('phone-focus',[t('focus-kicker','FOCUS THIS WEEK',10,'onaccent',600),t('focus-total','12h 40m',34,'onaccent',700),
            f('progress-track',[shape('progress-fill',224,7,'onaccent',radius=4)],'wash',w='fill',h=7,radius=4),
            t('focus-comparison','4h 20m ahead of last week',11,'onaccent')],fill='accent',w='fill',h=150,radius=20,layout={'pad':20},gap=9),
        row('phone-metrics',[{'id':'streak','type':'instance','of':'metric-tile','w':'fill'},
            {'id':'complete','type':'instance','of':'metric-tile','w':'fill','overrides':{'metric-label.text':'COMPLETED','metric-value.text':'18 tasks'}}],w='fill',h=74,gap=12),
        row('tasks-heading',[t('tasks-label','Up next',18,weight=700),t('tasks-all','View all',11,'accent',600)],w='fill',h=24,layout={'justify':'space-between'}),
        col('task-list',tasks,w='fill',h=166,gap=8),
        row('phone-pause',[col('pause-copy',[t('pause-title','A little breathing room',13,weight=600),
            t('pause-detail','Five quiet minutes count, too.',10,'muted')],w='fill',gap=7),
            f('pause-badge',[shape('pause-circle',48,48,'accent',kind='ellipse'),
                t('pause-time','5m',15,'onaccent',600,x=13,y=14)],w=48,h=48)],
            fill='wash',w='fill',h=88,radius=16,layout={'pad':16},gap=12),
        f('phone-spacer',h='fill',w='fill'),
        row('phone-navigation',nav,fill='panel',w='fill',h=62,radius=18,layout={'pad':12})],x=20,y=16,w=350,h=812,gap=12)
    return doc('A little more focus',390,844,[content],colors,{'metric-tile':metric})


def desktop():
    colors=palette(nav=('#16313c','#07141c'),navink=('#eff8f5','#eff8f5'),navmuted=('#b2ccc8','#b2ccc8'))
    nav=[]
    for i,label in enumerate(['Overview','Projects','My tasks','Team','Reports']):
        nav.append(row(f'side-nav-{i}',[shape(f'side-icon-{i}',12,12,'onaccent' if i==0 else 'navmuted',radius=3),
            t(f'side-label-{i}',label,13,'onaccent' if i==0 else 'navink',600 if i==0 else 400)],
            fill='accent' if i==0 else None,w='fill',h=42,radius=10,layout={'pad':[8,12]}))
    sidebar=f('desktop-sidebar',[
        row('brand',[shape('brand-mark',26,26,'accent',radius=8),t('brand-name','Fieldwork',21,'navink',700)],x=24,y=28,w=192,h=36),
        t('workspace-kicker','WORKSPACE',10,'navmuted',600,x=24,y=108),col('side-nav',nav,x=16,y=136,w=208,gap=6),
        t('sidebar-note','A calmer workspace.\nOne clear next step.',13,'navmuted',x=24,y=704,w=184),
        row('profile',[shape('profile-avatar',34,34,'wash',kind='ellipse'),col('profile-copy',[
            t('profile-name','Alex Morgan',12,'navink',600),t('profile-role','Personal workspace',10,'navmuted')],gap=4)],x=24,y=820,w=192)],'nav',w=240,h=900)
    stats=[]
    for i,(label,value,note) in enumerate([('FOCUS THIS WEEK','24h 16m','↑ 12% from last week'),('PROJECTS IN MOTION','06','2 ready for review'),('TASKS COMPLETED','42','A good rhythm this week')]):
        stats.append(col(f'stat-{i}',[t(f'stat-label-{i}',label,10,'muted',600),t(f'stat-value-{i}',value,29,weight=700),
            t(f'stat-note-{i}',note,11,'success')],fill='panel',w='fill',h=116,radius=16,layout={'pad':18},gap=7))
    bars=[]
    for i,(day,height) in enumerate(zip(['Mon','Tue','Wed','Thu','Fri','Sat','Sun'],[72,104,88,122,94,48,34])):
        bars.append(col(f'bar-{i}',[shape(f'bar-ink-{i}','fill',height,'accent' if i<5 else 'wash',radius=6),
            t(f'bar-day-{i}',day,10,'muted',w='fill',align='center')],w='fill',h=150,gap=8,layout={'justify':'end'}))
    activity=col('activity',[row('activity-heading',[t('activity-title','Focus, in the right places',17,weight=700),t('activity-period','THIS WEEK',10,'muted',600)],w='fill',layout={'justify':'space-between'}),
        row('activity-bars',bars,w='fill',h=150,gap=18,layout={'align':'end'})],fill='panel',w='fill',h=260,radius=16,layout={'pad':20},gap=24)
    projects=[]
    for i,(name,owner,status) in enumerate([('A more thoughtful dashboard','Alex','In progress'),('Desk companion','Kai','Ready for review'),('Spectator experience','Olu','Planning')]):
        projects.append(row(f'project-{i}',[col(f'project-copy-{i}',[t(f'project-name-{i}',name,12,weight=600),t(f'project-owner-{i}',owner+' · Updated today',10,'muted')],w='fill',gap=5),
            t(f'project-state-{i}',status,10,'accent',600,w=110,align='right')],w='fill',h=50))
    projects_panel=col('projects',[row('project-heading',[t('project-title','Active projects',17,weight=700),t('project-all','View all',11,'accent',600)],w='fill',layout={'justify':'space-between'}),
        *projects],fill='panel',w='fill',h='fill',radius=16,layout={'pad':20},gap=16)
    members=[]
    for i,(name,role) in enumerate([('Kai','Designing the next step'),('Olu','Reviewing the details'),('Pai','Keeping things moving')]):
        members.append(row(f'member-{i}',[shape(f'member-avatar-{i}',30,30,'wash',kind='ellipse'),
            col(f'member-copy-{i}',[t(f'member-name-{i}',name,12,weight=600),t(f'member-role-{i}',role,10,'muted')],gap=4)],w='fill',h=38,gap=10))
    team=col('team',[t('team-title','Team pulse',17,weight=700),*members],fill='panel',w='fill',h=220,radius=16,layout={'pad':20},gap=14)
    milestones=[]
    for i,(name,date) in enumerate([('Make the first draft','TODAY · 14:00'),('Share a clearer direction','THU · 10:30'),('Leave space for polish','FRI · 16:00')]):
        milestones.append(col(f'milestone-{i}',[t(f'milestone-date-{i}',date,9,'muted',600),t(f'milestone-name-{i}',name,12,weight=600)],w='fill',gap=6))
    upcoming=col('upcoming',[t('upcoming-title','A few good milestones',17,weight=700),*milestones,
        t('upcoming-note','Sample data. Make this space yours.',10,'muted',w='fill')],fill='panel',w='fill',h='fill',radius=16,layout={'pad':20},gap=24)
    main=col('desktop-main',[row('desktop-heading',[col('heading-copy',[t('desktop-title','Workspace overview',28,weight=700),
        t('desktop-subtitle','A clear view of the work ahead.',13,'muted')],gap=8),
        col('new-project',[t('new-project-label','+ New project',12,'onaccent',600)],fill='accent',w=136,h=42,radius=12,layout={'pad':12,'justify':'center','align':'center'})],w='fill',h=76,layout={'justify':'space-between'}),
        row('desktop-stats',stats,w='fill',h=116,gap=16),
        row('desktop-panels',[col('left-panels',[activity,projects_panel],w='fill',h='fill',gap=24),
            col('right-panels',[team,upcoming],w=300,h='fill',gap=24)],w='fill',h='fill',gap=24,layout={'align':'stretch'})],x=272,y=28,w=1136,h=844,gap=24)
    return doc('A clearer workspace',1440,900,[sidebar,main],colors)


def hud():
    colors=palette(bg=('#e4edf0','#07131e'),panel=('#ffffff','#142733'),ink=('#12303e','#f1f6fa'),
        muted=('#47616f','#b0c4d0'),stage=('#edf4f5','#0d212e'),platform=('#d9e8ec','#203f50'),
        grid=('#c4d7de','#294959'),red=('#b82e45','#ff8391'),blue=('#235dcc','#7db4ff'),
        gold=('#f0c76a','#f0c76a'),goldink=('#26303c','#26303c'))
    mono='JetBrains Mono'
    header=row('hud-header',[col('broadcast-brand',[t('arena-name','PERSONA ARENA',20,weight=700,family=mono),
        t('broadcast-state','● LIVE  /  SAMPLE MATCH',11,'success',600,family=mono)],w=360,gap=9),
        row('scoreboard',[t('north-label','NORTH',20,'red',700,family=mono),t('north-score','7',48,weight=700,family=mono),
            shape('score-divider',2,40,'line'),t('south-score','5',48,weight=700,family=mono),t('south-label','SOUTH',20,'blue',700,family=mono)],gap=24),
        col('round-clock',[t('round-label','ROUND 03 · BEST OF 5',11,'muted',600,family=mono,w='fill',align='right'),
            t('round-time','02:14',32,weight=600,family=mono,w='fill',align='right')],w=300,gap=5)],
        fill='panel',x=48,y=32,w=1824,h=104,radius=20,layout={'pad':[16,24],'justify':'space-between'})
    stagekids=[t('observer-label','OBSERVER CAMERA 01',13,'muted',600,family=mono,x=28,y=24),
        t('camera-mode','AUTO FOLLOW',11,'muted',600,family=mono,x=1144,y=26)]
    for i in range(7): stagekids.append(shape(f'grid-v-{i}',1,500,'grid',x=200+i*160,y=110))
    for i in range(4): stagekids.append(shape(f'grid-h-{i}',1120,1,'grid',x=120,y=180+i*120))
    stagekids.extend([shape('platform-north',280,160,'platform',x=200,y=180,radius=20),
        shape('platform-south',280,160,'platform',x=880,y=390,radius=20),
        shape('center-platform',180,180,'platform',x=590,y=270,radius=28,rotate=45),
        shape('objective-ring',88,88,'gold',kind='ellipse',x=636,y=316),
        t('objective-letter','A',38,'goldink',700,family=mono,x=668,y=332)])
    for i,(name,x,y,team) in enumerate([('KAI',380,360,'red'),('PAI',450,230,'red'),('OLU',860,390,'blue'),('RIN',980,500,'blue')]):
        stagekids.extend([shape(f'player-{i}',30,30,team,kind='ellipse',x=x,y=y),
            t(f'player-name-{i}',name,12,team,700,family=mono,x=x-10,y=y+38)])
    stagekids.append(col('objective-banner',[t('objective-copy','OBJECTIVE A · CONTESTED',13,'goldink',700,family=mono)],
        fill='gold',x=470,y=650,w=420,h=42,radius=12,layout={'align':'center','justify':'center'}))
    stage=f('arena-stage',stagekids,'stage',x=280,y=168,w=1360,h=728,radius=24,clip=True)
    roster=[t('roster-heading','NORTH',16,'red',700,family=mono),t('roster-subtitle','TEAM ROSTER',10,'muted',600,family=mono)]
    for i,(name,role) in enumerate([('Kai','SCOUT'),('Pai','SUPPORT'),('Milo','DEFENDER')]):
        roster.append(col(f'roster-{i}',[row(f'roster-name-row-{i}',[shape(f'roster-avatar-{i}',28,28,'red',kind='ellipse'),
            t(f'roster-name-{i}',name,15,weight=600)],gap=10),t(f'roster-role-{i}',role,9,'muted',600,family=mono),
            f(f'health-track-{i}',[shape(f'health-fill-{i}',140-i*20,5,'red',radius=3)],'line',w='fill',h=5,radius=3)],w='fill',gap=10))
    roster.extend([f('roster-space',w='fill',h='fill'),t('roster-footnote','3 PLAYERS ONLINE',9,'muted',600,family=mono)])
    left=col('roster',roster,fill='panel',x=48,y=168,w=208,h=728,radius=20,layout={'pad':20},gap=24)
    feed=[t('feed-title','MATCH FEED',13,weight=700,family=mono)]
    for i,(stamp,copy_) in enumerate([('00:45','Kai claimed\nObjective A'),('00:32','Olu joined\nthe south lane'),('00:18','Round 03\nbegan')]):
        feed.append(col(f'event-{i}',[t(f'event-time-{i}',stamp,10,'muted',600,family=mono),t(f'event-copy-{i}',copy_,13,weight=600,w='fill')],w='fill',gap=8))
    feed.extend([f('feed-space',h='fill'),t('feed-note','SAMPLE EVENT DATA',9,'muted',600,family=mono)])
    right=col('match-feed',feed,fill='panel',x=1664,y=168,w=208,h=728,radius=20,layout={'pad':20},gap=30)
    footer=row('hud-footer',[col('spectating',[t('spectating-label','Spectating Kai',21,weight=600),t('spectating-team','North · Scout',12,'muted')],w=360,gap=7),
        row('observer-controls',[t('speed','x1.0',14,weight=600,family=mono),t('free-camera','FREE CAMERA',12,'muted',600,family=mono),
            t('player-view','PLAYER VIEW',12,'accent',600,family=mono)],gap=36),
        t('hud-note','Sample data · customize this HUD',11,'muted',w=360,align='right')],fill='panel',x=48,y=928,w=1824,h=104,radius=20,
        layout={'pad':[20,24],'justify':'space-between'})
    return doc('Persona Arena · spectator HUD',1920,1080,[header,stage,left,right,footer],colors,dark=True)


_BUILDERS={'desk-buddy':desk_buddy,'phone-dashboard':phone,'desktop-workspace':desktop,'game-spectator-hud':hud}
_META=[('desk-buddy','Desk Buddy','A friendly companion with a focus timer and gentle next steps.',480,320,['companion','small-screen']),
       ('phone-dashboard','Phone dashboard','A focused day with progress, tasks and a compact navigation bar.',390,844,['mobile','dashboard']),
       ('desktop-workspace','Desktop workspace','A calm workspace with projects, activity and team updates.',1440,900,['desktop','dashboard']),
       ('game-spectator-hud','Persona game HUD','A broadcast-ready scoreboard, arena, roster and event feed.',1920,1080,['game','spectator'])]
_CATALOG={id:{'id':id,'revision':1,'title':title,'description':description,'width':w,'height':h,'tags':tags,'preview_node':'canvas'}
          for id,title,description,w,h,tags in _META}
_DOCUMENTS={id:build() for id,build in _BUILDERS.items()}


def list_templates():
    return copy.deepcopy(list(_CATALOG.values()))


def get_template(id,revision=None):
    if not isinstance(id,str) or id not in _DOCUMENTS or (revision is not None and (isinstance(revision,bool) or not isinstance(revision,int) or revision!=1)):
        raise UiDesignerError('UD-NOT-FOUND','Template id or retained revision not found.')
    return copy.deepcopy(_DOCUMENTS[id])
