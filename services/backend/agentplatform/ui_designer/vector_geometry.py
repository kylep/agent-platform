"""Bounded SVG fill geometry. Native Skia is imported only by worker booleans."""
from __future__ import annotations
import math
import re
from fontTools.pens.basePen import BasePen, NullPen
from fontTools.pens.boundsPen import BoundsPen
from fontTools.svgLib.path import parse_path
from fontTools.svgLib.path.arc import EllipticalArc
from .errors import invalid, issue

MAX_INPUT_BYTES = 64 << 10
MAX_OUTPUT_BYTES = 32 << 10
MAX_SEGMENTS = 2048
MAX_OUTPUT_SEGMENTS = 4096
MAX_COORDINATE = 100_000
_KINDS = ('union','subtract','intersect','exclude')
_TOKEN = re.compile(r'[MmLlHhVvCcSsQqTtAaZz]|[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?')


def _fail(message):
    raise invalid([issue('UD-VALUE','$.boolean',message)])


def _finite(value, nonnegative=False):
    if not isinstance(value,(int,float)) or isinstance(value,bool) or not math.isfinite(value) or abs(value)>MAX_COORDINATE or (nonnegative and value<0):
        _fail('Geometry must use finite bounded numeric coordinates.')
    return float(value)


def _lexical(d):
    # fontTools deliberately finds numeric substrings; ensure it cannot ignore
    # junk, malformed exponents or malformed comma separators around commands.
    if not isinstance(d,str): _fail('SVG path data must be a string.')
    try: encoded=d.encode()
    except UnicodeError: _fail('SVG path data must be valid UTF-8.')
    if len(encoded)>MAX_OUTPUT_BYTES:
        _fail('Each SVG path is at most 32 KiB.')
    previous=None; end=0
    for token in _TOKEN.finditer(d):
        gap=d[end:token.start()]
        if gap.strip(' \t\r\n,') or gap.count(',')>1 or (',' in gap and
                (previous is None or previous.isalpha() or token.group().isalpha())):
            _fail('Malformed SVG path syntax.')
        previous=token.group();end=token.end()
    if previous is not None and previous.isalpha() and previous not in ('Z','z'):
        _fail('SVG drawing commands need their coordinate arguments.')
    if d[end:].strip() or (d.strip() and (previous is None or not d.lstrip().startswith(('M','m')))):
        _fail('Malformed SVG path syntax; paths begin with M or m.')


class _Pen(BasePen):
    def __init__(self,out,budget,limit,transform=None):
        super().__init__(None)
        self.out,self.budget,self.limit=out,budget,limit
        self.transform=transform or (1,0,0,1,0,0)
        self.current=self.start=None
    def count(self):
        self.budget[0]+=1
        if self.budget[0]>self.limit: _fail('SVG path segment budget exceeded.')
    def point(self,p):
        x,y=(_finite(v) for v in p)
        a,b,c,d,e,f=self.transform
        return (_finite(a*x+c*y+e),_finite(b*x+d*y+f))
    def _moveTo(self,p): self.count();self.out.moveTo(self.point(p));self.current=self.start=p
    def _lineTo(self,p): self.count();self.out.lineTo(self.point(p));self.current=p
    def _curveToOne(self,a,b,c): self.count();self.out.curveTo(self.point(a),self.point(b),self.point(c));self.current=c
    def _qCurveToOne(self,a,b): self.count();self.out.qCurveTo(self.point(a),self.point(b));self.current=b
    def _closePath(self): self.count();self.out.closePath();self.current=self.start
    def _endPath(self): self._closePath()  # SVG fill implicitly closes contours
    def arcTo(self,rx,ry,rotation,large,sweep,target):
        rx,ry=_finite(rx,True),_finite(ry,True)
        _finite(rotation);self.point(target)
        if large not in (0,1) or sweep not in (0,1): _fail('Arc flags must be zero or one.')
        if rx==0 or ry==0:
            self.lineTo(target);return
        if self.current is None: _fail("Arc needs a current point.")
        EllipticalArc(complex(*self.current),rx,ry,rotation,large,sweep,complex(*target)).draw(self)


def _parse(d,out,budget,limit,transform=None):
    _lexical(d)
    try:
        parse_path(d,_Pen(out,budget,limit,transform))
    except (ValueError,TypeError,IndexError,AssertionError,ZeroDivisionError) as error:
        _fail('Malformed or unsupported SVG path geometry.')


def validate_path(d, *, max_segments=16_384):
    """Strict path validation without importing Skia or normalizing the source."""
    _parse(d,NullPen(),[0],max_segments)


def validate_operands(operands,kind):
    if kind not in _KINDS or not isinstance(operands,list) or not 2<=len(operands)<=8:
        _fail('Choose union, subtract, intersect or exclude with 2 to 8 operands.')
    total=0
    for node in operands:
        if not isinstance(node,dict): _fail('Operands must be geometry objects.')
        type_=node.get('type')
        allowed={'type','x','y','w','h','rotate'} | ({'d'} if type_=='path' else {'radius'} if type_=='rect' else set())
        if type_ not in ('path','rect','ellipse') or set(node)-allowed or not {'type','x','y','w','h'}<=set(node):
            _fail('Operands accept only path, rect or ellipse geometry fields.')
        for key in ('x','y','w','h','rotate'):
            if key in node: _finite(node[key],key in ('w','h'))
        empty=type_=='path' and node.get('d')==''
        if not empty and (node['w']<=0 or node['h']<=0): _fail('Nonempty operands need positive fixed width and height.')
        if type_=='path':
            _lexical(node.get('d'))
            total+=len(node['d'].encode())
        if 'radius' in node:
            radii=node['radius'] if isinstance(node['radius'],list) else [node['radius']]
            if len(radii) not in (1,4): _fail('Rect radius is a number or four corner radii.')
            for radius in radii: _finite(radius,True)
    if total>MAX_INPUT_BYTES: _fail('Boolean input paths total at most 64 KiB.')


def _format(number):
    value=f'{number:.6f}'.rstrip('0').rstrip('.')
    return '0' if value in ('','-0') else value


class _SVGOutput(BasePen):
    """Canonical M/L/C/Z output is immediately editable by the web pen tool."""
    def __init__(self):
        super().__init__(None);self.parts=[]
    def coordinates(self,*points):
        return ' '.join(_format(v) for point in points for v in point)
    def _moveTo(self,p): self.parts.append('M'+self.coordinates(p))
    def _lineTo(self,p): self.parts.append('L'+self.coordinates(p))
    def _curveToOne(self,a,b,c): self.parts.append('C'+self.coordinates(a,b,c))
    def _qCurveToOne(self,control,end):
        start=self._getCurrentPoint()
        a=tuple(start[i]+2*(control[i]-start[i])/3 for i in range(2))
        b=tuple(end[i]+2*(control[i]-end[i])/3 for i in range(2))
        self._curveToOne(a,b,end)
    def _closePath(self): self.parts.append('Z')
    def _endPath(self): self._closePath()
    def getCommands(self): return ''.join(self.parts)


def boolean_geometry(operands,kind):
    """Fill outlines only; strokes/effects are retained by the API, not expanded.

    Paths' w/h specify their rotation pivot, never a scale for d. Input curves
    and rounded/elliptical outlines use bounded cubic representations. Holes
    retain nonzero winding through Skia's winding-fixing output.
    """
    validate_operands(operands,kind)
    import pathops  # native work belongs only in the isolated renderer worker
    from .svg import _shape_d
    budget=[0];paths=[]
    for node in operands:
        path=pathops.Path(); path.fillType=pathops.FillType.WINDING
        angle=math.radians(node.get('rotate',0));c,s=math.cos(angle),math.sin(angle)
        x,y,w,h=(node[key] for key in ('x','y','w','h'))
        transform=(c,s,-s,c,x+w/2-c*w/2+s*h/2,y+h/2-s*w/2-c*h/2)
        d=node['d'] if node['type']=='path' else _shape_d({**node,'x':0,'y':0})
        _parse(d,path.getPen(),budget,MAX_SEGMENTS,transform)
        paths.append(path)
    operation={'union':pathops.PathOp.UNION,'subtract':pathops.PathOp.DIFFERENCE,
               'intersect':pathops.PathOp.INTERSECTION,'exclude':pathops.PathOp.XOR}[kind]
    result=paths[0]
    try:
        for path in paths[1:]:
            result=pathops.op(result,path,operation,fix_winding=True,keep_starting_points=False)
        result.convertConicsToQuads(.01)
        bounds=BoundsPen(None)
        result.draw(_Pen(bounds,[0],MAX_OUTPUT_SEGMENTS))
        if bounds.bounds is None:
            return {'geometry':{'x':operands[0]['x'],'y':operands[0]['y'],'w':0,'h':0,'d':'','empty':True},'warnings':[]}
        x0,y0,x1,y1=bounds.bounds
        for value in (x0,y0,x1,y1,x1-x0,y1-y0): _finite(value)
        output=_SVGOutput()
        result.draw(_Pen(output,[0],MAX_OUTPUT_SEGMENTS,(1,0,0,1,-x0,-y0)))
        d=output.getCommands()
    except (ValueError,RuntimeError,TypeError,OverflowError):
        _fail('Boolean geometry could not be computed within the supported geometry model.')
    if len(d.encode())>MAX_OUTPUT_BYTES: _fail('Boolean result exceeds 32 KiB.')
    return {'geometry':{'x':x0,'y':y0,'w':x1-x0,'h':y1-y0,'d':d,'empty':False},
            'warnings':['Boolean operations use fill outlines; ellipse and arc curves use cubic approximations.']}
