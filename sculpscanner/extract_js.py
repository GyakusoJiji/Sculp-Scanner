"""ページ内に注入する JavaScript。

想定しているページ構造（2026-07 時点の www.honda.co.jp/*/configurator/ で確認）:

    window.pc                                   … PlayCanvas ランタイム
    pc.Application.getApplication()             … 稼働中のアプリ
    app.root                                    … シーングラフのルート Entity
    entity.render.meshInstances[]               … 描画メッシュ（新 API）
    entity.model.meshInstances[]                … 同上（旧 API）
    meshInstance.mesh.getPositions() / getIndices()
    meshInstance.node.getWorldTransform().data  … 列優先 4x4

Draco 圧縮された GLB もランタイム上では展開済みのため、
ここではデコード済みの頂点をそのまま読み出せる。

公開 API（すべて window.__SCULP__ 配下）:
    scan()                       -> パートのメタ情報配列
    chunk(id, kind, off, len)    -> base64 文字列（kind = 'pos' | 'idx'）
    release()                    -> 抽出済みバッファを解放
"""

# 除外の既定パターン（背景・壁・床・スカイスフィア・影）
DEFAULT_EXCLUDE_PATTERN = r"background|wall|floor|ground|sphere|panorama|skybox|shadow|kage|haikei"

# 除外対象のレイヤー ID（config.json の layers より: 2=Skybox, 3=Immediate, 4=UI, 1002=UI2）
EXCLUDE_LAYERS = [2, 3, 4, 1002]

# 世界空間 AABB がこのサイズ(m)を超えるものは背景とみなして既定で除外
MAX_PART_SIZE_M = 20.0


def install_js(exclude_pattern: str = DEFAULT_EXCLUDE_PATTERN,
               exclude_layers=None,
               max_part_size: float = MAX_PART_SIZE_M) -> str:
    """window.__SCULP__ を定義する JS（Playwright に渡すアロー関数）を返す。"""
    layers = EXCLUDE_LAYERS if exclude_layers is None else exclude_layers
    return (
        "() => {\n"
        f"  const EXCLUDE_RE = /{exclude_pattern}/i;\n"
        f"  const EXCLUDE_LAYERS = {list(layers)!r};\n"
        f"  const MAX_SIZE = {float(max_part_size)!r};\n"
        + _BODY
        + "\n  return 'ok';\n}"
    )


_BODY = r"""
  function getApp() {
    if (!window.pc) throw new Error('window.pc が見つかりません（PlayCanvas 製ページではない可能性があります）');
    var app = null;
    if (pc.Application && pc.Application.getApplication) app = pc.Application.getApplication();
    if (!app && pc.app) app = pc.app;
    if (!app) throw new Error('稼働中の PlayCanvas Application が見つかりません');
    return app;
  }

  // --- 頂点位置の読み出し -------------------------------------------------
  // 第一手段: Mesh#getPositions()
  // 失敗時 : vertexBuffer を format.elements の offset/stride に従って直接読む
  function readPositions(mesh) {
    try {
      var arr = [];
      mesh.getPositions(arr);
      if (arr && arr.length) return new Float32Array(arr);
    } catch (e) { /* フォールバックへ */ }

    var vb = mesh.vertexBuffer;
    if (!vb) return null;
    var fmt = vb.getFormat ? vb.getFormat() : vb.format;
    if (!fmt || !fmt.elements) return null;

    var el = null;
    for (var i = 0; i < fmt.elements.length; i++) {
      if (fmt.elements[i].name === pc.SEMANTIC_POSITION) { el = fmt.elements[i]; break; }
    }
    if (!el) return null;

    var storage = vb.storage || (vb.lock ? vb.lock() : null);
    if (!storage) return null;
    var dv = new DataView(storage);
    var n = vb.getNumVertices ? vb.getNumVertices() : vb.numVertices;
    var stride = el.stride || fmt.size;
    var base = el.offset || 0;
    var out = new Float32Array(n * 3);
    for (var v = 0; v < n; v++) {
      var b = base + v * stride;
      out[v * 3]     = dv.getFloat32(b,     true);
      out[v * 3 + 1] = dv.getFloat32(b + 4, true);
      out[v * 3 + 2] = dv.getFloat32(b + 8, true);
    }
    return out;
  }

  // --- 三角形インデックスの読み出し ---------------------------------------
  // 注意: Mesh#getIndices(arr) は「件数」を返し配列 arr を埋める API であって
  //       インデックス配列を返さない。戻り値をそのまま使うと 0,1,2,... の
  //       連番フォールバックに落ちて頂点数を超えるインデックスが出来てしまう。
  //       そのため IndexBuffer を直接読むのを第一手段にする。
  function readIndexBuffer(mesh) {
    var ib = mesh.indexBuffer && mesh.indexBuffer[0];
    if (!ib) return null;
    var storage = ib.storage || (ib.lock ? ib.lock() : null);
    if (!storage) return null;
    var n = ib.getNumIndices ? ib.getNumIndices() : ib.numIndices;
    var bpi = ib.bytesPerIndex;
    if (!bpi) {
      var fmt = ib.getFormat ? ib.getFormat() : ib.format;
      bpi = (fmt === pc.INDEXFORMAT_UINT32) ? 4 : (fmt === pc.INDEXFORMAT_UINT8 ? 1 : 2);
    }
    if (bpi === 4) return new Uint32Array(storage, 0, n);
    if (bpi === 1) return new Uint8Array(storage, 0, n);
    return new Uint16Array(storage, 0, n);
  }

  function readIndices(mesh, numVerts) {
    var prim = (mesh.primitive && mesh.primitive[0]) ? mesh.primitive[0] : null;
    if (prim && prim.type !== undefined && prim.type !== pc.PRIMITIVE_TRIANGLES) return null;

    var base = prim ? (prim.base | 0) : 0;
    var count = prim ? (prim.count | 0) : 0;

    var src = null;
    try { src = readIndexBuffer(mesh); } catch (e) { src = null; }
    if (!src) {
      // 保険: getIndices(arr) 形式（arr を埋め、件数を返す）
      try {
        var tmp = [];
        var got = mesh.getIndices(tmp);
        if (tmp.length) src = tmp;
        else if (got && got.length) src = got;   // 配列を返す旧実装向け
      } catch (e2) { src = null; }
    }

    if (src && src.length) {
      if (!count) count = src.length - base;
      count = Math.min(count, src.length - base);
      var out = new Uint32Array(count);
      for (var i = 0; i < count; i++) out[i] = src[base + i];
      return out;
    }

    // 本当に非インデックス描画の場合のみ 0,1,2,... を生成
    if (!count) count = numVerts;
    count = Math.min(count, numVerts - base);
    if (count < 3) return null;
    var seq = new Uint32Array(count);
    for (var j = 0; j < count; j++) seq[j] = base + j;
    return seq;
  }

  function layerExcluded(comp) {
    var ls = comp && comp.layers;
    if (!ls || !ls.length) return false;
    for (var i = 0; i < ls.length; i++) {
      if (EXCLUDE_LAYERS.indexOf(ls[i]) === -1) return false;  // 1つでも通常レイヤーがあれば採用
    }
    return true;
  }

  function entityPath(e) {
    var names = [];
    var n = e;
    while (n && n.name !== undefined) { names.unshift(n.name); n = n.parent; }
    return names.join('/');
  }

  var PARTS = [];

  function scan() {
    var app = getApp();
    PARTS = [];
    var meta = [];
    var stack = [app.root];

    while (stack.length) {
      var e = stack.pop();
      if (!e) continue;
      if (e.enabled === false) continue;          // 無効な枝ごとスキップ
      var children = e.children || [];
      for (var ci = 0; ci < children.length; ci++) stack.push(children[ci]);

      var c = e.c || {};
      var groups = [];
      if (c.render && c.render.enabled !== false && c.render.meshInstances) groups.push([c.render, c.render.meshInstances]);
      if (c.model && c.model.enabled !== false && c.model.meshInstances) groups.push([c.model, c.model.meshInstances]);

      for (var gi = 0; gi < groups.length; gi++) {
        var comp = groups[gi][0], mis = groups[gi][1];
        if (layerExcluded(comp)) continue;

        for (var mi = 0; mi < mis.length; mi++) {
          var inst = mis[mi];
          if (!inst || inst.visible === false || !inst.mesh) continue;

          var pos = readPositions(inst.mesh);
          if (!pos || !pos.length) continue;
          var idx = readIndices(inst.mesh, pos.length / 3);
          if (!idx || idx.length < 3) continue;

          var maxIdx = 0;
          for (var q = 0; q < idx.length; q++) if (idx[q] > maxIdx) maxIdx = idx[q];

          var node = inst.node || e;
          var m = node.getWorldTransform().data;   // 列優先 16 要素
          var world = [];
          for (var k = 0; k < 16; k++) world.push(m[k]);

          // 世界空間 AABB（背景判定と表示用）
          var size = null, center = null;
          try {
            var ab = inst.aabb;
            var he = ab.halfExtents, ct = ab.center;
            size = [he.x * 2, he.y * 2, he.z * 2];
            center = [ct.x, ct.y, ct.z];
          } catch (err) { /* 取れなくても続行 */ }

          var path = entityPath(e);
          var big = size ? Math.max(size[0], size[1], size[2]) > MAX_SIZE : false;
          var excluded = EXCLUDE_RE.test(path) || big;

          var id = PARTS.length;
          PARTS.push({ pos: pos, idx: idx });
          meta.push({
            id: id,
            name: e.name,
            path: path,
            tris: Math.floor(idx.length / 3),
            verts: Math.floor(pos.length / 3),
            maxIdx: maxIdx,
            posBytes: pos.byteLength,
            idxBytes: idx.byteLength,
            world: world,
            size: size,
            center: center,
            excluded: excluded,
            material: (inst.material && inst.material.name) || ''
          });
        }
      }
    }
    return meta;
  }

  function b64(u8) {
    var CH = 0x8000, s = '';
    for (var i = 0; i < u8.length; i += CH) {
      s += String.fromCharCode.apply(null, u8.subarray(i, i + CH));
    }
    return btoa(s);
  }

  function chunk(id, kind, off, len) {
    var p = PARTS[id];
    if (!p) throw new Error('パート ' + id + ' が見つかりません（scan をやり直してください）');
    var buf = (kind === 'pos') ? p.pos : p.idx;
    var avail = buf.byteLength - off;
    if (avail <= 0) return '';
    var u8 = new Uint8Array(buf.buffer, buf.byteOffset + off, Math.min(len, avail));
    return b64(u8);
  }

  function meshCount() {
    var app;
    try { app = getApp(); } catch (e) { return -1; }
    var n = 0, stack = [app.root];
    while (stack.length) {
      var e = stack.pop();
      if (!e || e.enabled === false) continue;
      var ch = e.children || [];
      for (var i = 0; i < ch.length; i++) stack.push(ch[i]);
      var c = e.c || {};
      if (c.render && c.render.meshInstances) n += c.render.meshInstances.length;
      if (c.model && c.model.meshInstances) n += c.model.meshInstances.length;
    }
    return n;
  }

  window.__SCULP__ = {
    scan: scan,
    chunk: chunk,
    meshCount: meshCount,
    release: function () { PARTS = []; return true; }
  };
"""

# PlayCanvas アプリが存在するかの判定式（フレーム探索用）
HAS_PC_JS = (
    "() => !!(window.pc && ((pc.Application && pc.Application.getApplication "
    "&& pc.Application.getApplication()) || pc.app))"
)
