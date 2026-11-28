"""The village map: fixed 64x40 terrain, landmarks, starting sources, A*.

Pure and deterministic (plan §2 World model, §3 Movement). The map itself never
changes; the world overlays two sparse sets on it: built stone-path tiles and
footbridge tiles (which make a stream tile walkable).
"""
import heapq
from functools import lru_cache

from .contract import MAP_H, MAP_W

# One-letter terrain codes.
GRASS, WOODS, HILLSIDE, STREAM, POND, REEDBED = "G", "W", "H", "S", "P", "R"
SHORE, DOCK, GARDEN, WORKSHOP, SQUARE, PATH, FARBANK, BRIDGE = "A", "D", "N", "K", "Q", "T", "F", "B"

TERRAIN_NAMES = {
    GRASS: "grass", WOODS: "woods", HILLSIDE: "hillside", STREAM: "stream", POND: "pond",
    REEDBED: "reed bed", SHORE: "shore", DOCK: "dock", GARDEN: "garden", WORKSHOP: "workshop",
    SQUARE: "square", PATH: "stone path", FARBANK: "far bank", BRIDGE: "footbridge",
}

# Movement cost per tile (10 = plain); stone paths are quicker.
_COST = {GRASS: 10, WOODS: 14, HILLSIDE: 16, REEDBED: 13, SHORE: 10, DOCK: 8, GARDEN: 10,
         WORKSHOP: 8, SQUARE: 8, PATH: 8, FARBANK: 10, BRIDGE: 8}
CART_OK = frozenset({PATH, SQUARE, WORKSHOP, DOCK, BRIDGE})   # a cart stays on built ground

BRIDGE_SITE = (30, 6)        # the one stream tile a footbridge can span
STREAM_ROW = 6

LANDMARKS: dict[str, tuple[int, int]] = {
    "square": (32, 20), "pond": (46, 15), "dock": (41, 15), "garden": (22, 30),
    "workshop": (38, 27), "woods": (10, 14), "hillside": (56, 30), "shore": (43, 17),
    "stream": (20, 6), "far_bank": (30, 3), "bridge_site": BRIDGE_SITE, "pile": (34, 22),
}


def _build() -> list[list[str]]:
    g = [[GRASS] * MAP_W for _ in range(MAP_H)]
    for y in range(MAP_H):
        for x in range(MAP_W):
            if y <= 5:
                g[y][x] = FARBANK
            elif y == STREAM_ROW:
                g[y][x] = STREAM
            elif 3 <= x <= 20 and 8 <= y <= 26 and (x * 7 + y * 13) % 5 != 0:
                g[y][x] = WOODS
            elif x >= 50 and y >= 24:
                g[y][x] = HILLSIDE
    cx, cy = 48, 15
    for y in range(MAP_H):
        for x in range(MAP_W):
            d = ((x - cx) ** 2) / 36 + ((y - cy) ** 2) / 16
            ring = ((x - cx) ** 2) / 64 + ((y - cy) ** 2) / 36
            if d <= 1:
                g[y][x] = POND
            elif ring <= 1 and x <= cx:
                g[y][x] = REEDBED
            elif ((x - cx) ** 2) / 100 + ((y - cy) ** 2) / 64 <= 1 and g[y][x] == GRASS:
                g[y][x] = SHORE
    for y in range(17, 24):
        for x in range(28, 37):
            g[y][x] = SQUARE
    for y in range(26, 29):
        for x in range(36, 41):
            g[y][x] = WORKSHOP
    for y in range(28, 33):
        for x in range(20, 26):
            g[y][x] = GARDEN
    for x in range(37, 48):
        g[20][x] = PATH
    for y in range(24, 31):
        g[y][32] = PATH
    for x in range(33, 36):
        g[27][x] = PATH
    for x in range(26, 32):
        g[30][x] = PATH
    g[15][41] = g[15][42] = DOCK
    g[STREAM_ROW][BRIDGE_SITE[0]] = STREAM
    return g


_GRID = _build()


def base_terrain(x: int, y: int) -> str:
    return _GRID[y][x]


def in_bounds(x: int, y: int) -> bool:
    return 0 <= x < MAP_W and 0 <= y < MAP_H


def terrain_at(x: int, y: int, bridge=frozenset(), paths=frozenset()) -> str:
    if (x, y) in bridge:
        return BRIDGE
    if (x, y) in paths:
        return PATH
    return _GRID[y][x]


def passable(x: int, y: int, bridge=frozenset(), paths=frozenset(), cart: bool = False) -> bool:
    if not in_bounds(x, y):
        return False
    t = terrain_at(x, y, bridge, paths)
    if t in (STREAM, POND):
        return False
    if cart and t not in CART_OK:
        return False
    return True


def cheb(ax: int, ay: int, bx: int, by: int) -> int:
    return max(abs(ax - bx), abs(ay - by))


def manhattan(ax: int, ay: int, bx: int, by: int) -> int:
    return abs(ax - bx) + abs(ay - by)


def _tiles(code: str) -> list[tuple[int, int]]:
    return [(x, y) for y in range(MAP_H) for x in range(MAP_W) if _GRID[y][x] == code]


def _nearest(codes: str, x: int, y: int) -> tuple[int, int]:
    best = None
    for yy in range(MAP_H):
        for xx in range(MAP_W):
            if _GRID[yy][xx] in codes:
                d = (manhattan(x, y, xx, yy), yy, xx)
                if best is None or d < best:
                    best = d
    return best[2], best[1]


def starting_sources() -> list[dict]:
    """Source rows (id, kind, material, tile) for a fresh world, in id order."""
    from .materials import SOURCE_KINDS
    out: list[dict] = []

    def add(kind: str, n: int, x: int, y: int) -> None:
        out.append({"id": f"{kind}{n}", "kind": kind, "m": SOURCE_KINDS[kind]["m"], "x": x, "y": y})

    trees = [t for t in _tiles(WOODS) if (t[0] * 3 + t[1] * 5) % 11 == 0][:12]
    for i, (x, y) in enumerate(trees, 1):
        add("tree", i, x, y)
    reeds = _tiles(REEDBED)
    step = max(1, len(reeds) // 6)
    for i, (x, y) in enumerate(reeds[::step][:6], 1):
        add("reed_patch", i, x, y)
    add("stone_pile", 1, *_nearest(SHORE + GRASS, 47, 20))
    for i, (nx, ny) in enumerate([(22, 12), (22, 22), (40, 12), (36, 14)], 1):
        add("seed_spot", i, *_nearest(GRASS, nx, ny))
    add("clay_bank", 1, 30, 3)
    return out


# --- A* ---------------------------------------------------------------------

_MAX_EXPAND = 6000


@lru_cache(maxsize=4096)
def _astar(sx, sy, gx, gy, stop, bridge, paths, cart) -> tuple | None:
    def goal(x, y):
        return cheb(x, y, gx, gy) <= stop and passable(x, y, bridge, paths, cart)

    if goal(sx, sy):
        return ()
    open_ = [(0, 0, sx, sy)]
    g = {(sx, sy): 0}
    came: dict[tuple[int, int], tuple[int, int]] = {}
    seen = 0
    while open_:
        _, _, x, y = heapq.heappop(open_)
        if goal(x, y):
            path = [(x, y)]
            while (x, y) in came:
                x, y = came[(x, y)]
                path.append((x, y))
            path.pop()
            return tuple(reversed(path))
        seen += 1
        if seen > _MAX_EXPAND:
            return None
        for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
            nx, ny = x + dx, y + dy
            if not passable(nx, ny, bridge, paths, cart):
                continue
            cost = g[(x, y)] + _COST[terrain_at(nx, ny, bridge, paths)]
            if cost < g.get((nx, ny), 1 << 30):
                g[(nx, ny)] = cost
                came[(nx, ny)] = (x, y)
                h = max(0, manhattan(nx, ny, gx, gy) - stop) * 8
                heapq.heappush(open_, (cost + h, h, nx, ny))
    return None


def find_path(start, goal, stop: int = 0, bridge=frozenset(), paths=frozenset(),
              cart: bool = False) -> list[tuple[int, int]] | None:
    """Tiles to walk (excluding the start), [] when already there, None when
    there is no way. Cached on (start, goal, bridge, paths, cart)."""
    r = _astar(start[0], start[1], goal[0], goal[1], stop, frozenset(bridge), frozenset(paths), cart)
    return None if r is None else list(r)
