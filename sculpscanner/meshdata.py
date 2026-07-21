"""抽出したパートの組み立て・座標変換・STL 書き出し。"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field

import numpy as np
import pyvista as pv


@dataclass
class Part:
    """PlayCanvas の meshInstance 1 つ分。"""

    id: int
    name: str
    path: str
    tris: int
    verts: int
    world: np.ndarray = field(repr=False)          # 4x4（行優先に直したもの）
    size: tuple | None = None                      # 世界空間 AABB のサイズ(m)
    excluded: bool = False
    material: str = ""
    positions: np.ndarray | None = field(default=None, repr=False)   # (N,3) float32
    indices: np.ndarray | None = field(default=None, repr=False)     # (M,3) uint32

    @property
    def loaded(self) -> bool:
        return self.positions is not None and self.indices is not None


def part_from_meta(meta: dict) -> Part:
    """ページから返ってきたメタ情報を Part に変換する。

    PlayCanvas の Mat4#data は列優先なので、転置して行優先の 4x4 にする。
    """
    world = np.asarray(meta["world"], dtype=np.float64).reshape(4, 4).T
    size = tuple(meta["size"]) if meta.get("size") else None
    return Part(
        id=meta["id"],
        name=meta.get("name") or f"part{meta['id']}",
        path=meta.get("path", ""),
        tris=int(meta["tris"]),
        verts=int(meta["verts"]),
        world=world,
        size=size,
        excluded=bool(meta.get("excluded")),
        material=meta.get("material", ""),
    )


def decode_positions(b64_parts: list[str]) -> np.ndarray:
    raw = b"".join(base64.b64decode(p) for p in b64_parts)
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, 3)


def decode_indices(b64_parts: list[str]) -> np.ndarray:
    raw = b"".join(base64.b64decode(p) for p in b64_parts)
    idx = np.frombuffer(raw, dtype=np.uint32)
    return idx[: (len(idx) // 3) * 3].reshape(-1, 3)


# --- キャッシュ（再スクレイプせずに出力設定を試すため） --------------------

def save_cache(parts: list[Part], path: str) -> None:
    meta = []
    arrays = {}
    for p in parts:
        meta.append({
            "id": p.id, "name": p.name, "path": p.path, "tris": p.tris, "verts": p.verts,
            "world": p.world.tolist(), "size": list(p.size) if p.size else None,
            "excluded": p.excluded, "material": p.material, "loaded": p.loaded,
        })
        if p.loaded:
            arrays[f"p{p.id}"] = p.positions
            arrays[f"i{p.id}"] = p.indices
    np.savez_compressed(path, meta=np.array(json.dumps(meta)), **arrays)


def load_cache(path: str) -> list[Part]:
    z = np.load(path, allow_pickle=False)
    meta = json.loads(str(z["meta"]))
    parts = []
    for m in meta:
        p = Part(
            id=m["id"], name=m["name"], path=m["path"], tris=m["tris"], verts=m["verts"],
            world=np.asarray(m["world"], dtype=np.float64),
            size=tuple(m["size"]) if m["size"] else None,
            excluded=m["excluded"], material=m["material"],
        )
        if m["loaded"]:
            p.positions = z[f"p{p.id}"]
            p.indices = z[f"i{p.id}"]
        parts.append(p)
    return parts


# --- 組み立て -------------------------------------------------------------

def build_mesh(
    parts: list[Part],
    *,
    up: str = "Z",
    unit_scale: float = 1000.0,
    weld: bool = True,
    decimate: float = 0.0,
) -> pv.PolyData:
    """included かつ読み込み済みのパートを 1 つの PolyData にまとめる。

    up:         "Z" なら PlayCanvas の Y-up を Z-up（3D プリント慣習）へ回す。"Y" ならそのまま。
    unit_scale: シーン単位(m)への倍率。1000.0 で mm。
    weld:       重複頂点の溶接と退化三角形の除去。
    decimate:   0.0〜0.95 の間引き率。
    """
    chunks_p: list[np.ndarray] = []
    chunks_f: list[np.ndarray] = []
    offset = 0
    dropped = 0

    for p in parts:
        if p.excluded or not p.loaded:
            continue
        v = p.positions.astype(np.float64)
        m = p.world
        v = v @ m[:3, :3].T + m[:3, 3]

        f = p.indices.astype(np.int64)
        # 範囲外インデックス（GLB の共有頂点バッファ等で稀に発生）を落とす。
        # 残すと vtkCleanPolyData がアクセス違反で落ちる。
        ok = (f >= 0).all(axis=1) & (f < len(v)).all(axis=1)
        if not ok.all():
            dropped += int((~ok).sum())
            f = f[ok]
        if len(f) == 0:
            continue

        chunks_p.append(v)
        chunks_f.append(f + offset)
        offset += len(v)

    if dropped:
        print(f"[警告] 範囲外インデックスの三角形 {dropped:,} 個を除外しました")

    if not chunks_p:
        raise ValueError("出力対象のパートがありません（すべて除外されています）")

    points = np.vstack(chunks_p)
    faces = np.vstack(chunks_f)

    if up.upper() == "Z":
        # PlayCanvas: X right / Y up / -Z forward  ->  Z-up 右手系
        points = np.column_stack([points[:, 0], -points[:, 2], points[:, 1]])

    points = points * float(unit_scale)

    cells = np.hstack([np.full((len(faces), 1), 3, dtype=np.int64), faces]).ravel()
    mesh = pv.PolyData(points, cells)

    if weld:
        mesh = mesh.clean(point_merging=True, lines_to_points=False, polys_to_lines=False)
        mesh = mesh.triangulate()

    if decimate and decimate > 0:
        mesh = mesh.decimate_pro(min(float(decimate), 0.95), preserve_topology=True)

    return mesh


def stats(mesh: pv.PolyData) -> dict:
    b = mesh.bounds
    size = (b[1] - b[0], b[3] - b[2], b[5] - b[4])
    try:
        manifold = bool(mesh.is_manifold)
    except Exception:
        manifold = False
    return {
        "points": int(mesh.n_points),
        "triangles": int(mesh.n_cells),
        "size": size,
        "manifold": manifold,
    }


def format_stats(st: dict) -> str:
    sx, sy, sz = st["size"]
    return (
        f"三角形 {st['triangles']:,} / 頂点 {st['points']:,}\n"
        f"寸法 {sx:.1f} x {sy:.1f} x {sz:.1f} mm\n"
        f"水密(manifold): {'はい' if st['manifold'] else 'いいえ'}"
    )


def save_stl(mesh: pv.PolyData, path: str, binary: bool = True) -> None:
    mesh.save(path, binary=binary)
