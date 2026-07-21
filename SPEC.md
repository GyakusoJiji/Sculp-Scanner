# Sculp-Scanner 使い方・仕様書

Chrome で開いた Honda の 360°ビュー（コンフィギュレータ）から、表示中の車両の 3D データを
抽出して STL として保存するアプリケーションの、操作手順と内部仕様。

- 対象: `https://www.honda.co.jp/<車種>/configurator/`（PlayCanvas 製の 360°ビュー）
- 最終更新: 2026-07-22
- 概要と要点は [README.md](README.md) を参照

---

## 1. 前提となる調査結果

Honda の 360°ビューは**連番画像を切り替える方式ではない**。ページを解析した結果:

| 項目 | 実際の内容 |
|---|---|
| 実装 | `playcanvas-stable.min.js` + `__settings__.js` + `config.json` + シーン `2340474.json` |
| 種別 | **PlayCanvas（WebGL）で動作する実 3D アプリケーション** |
| 3D アセット | container 型 GLB（`CIVIC_RS.glb` 8.5MB、`FRONT_HEADLIGHT.glb`、`CIVIC_OPT_PARTS.glb` ほか） |
| 圧縮 | `PRELOAD_MODULES` に `DracoDecoderModule` → GLB は Draco 圧縮 |
| 背景 | `background*.glb` / `211219_wall_v2.glb`（車両とは別エンティティ） |
| カメラ | `Camera` エンティティ + `orbitCamera` / `cameraMultiframe` スクリプト |

このため本アプリは、画像からの三次元復元（visual hull 等）ではなく
**稼働中の PlayCanvas シーングラフから直接メッシュを読み出す**方式を採る。

**この方式を選んだ理由**

| 代替案 | 不採用の理由 |
|---|---|
| GLB を URL から直接ダウンロードして解析 | Draco 圧縮の Python デコーダが無い／ワールド変換が不明／表示中のグレード・オプション構成が反映されない |
| 多視点画像からの visual hull | 窓の奥やホイールハウス等の凹形状が復元できず、実物とは別物の外形しか得られない |
| **シーングラフからのライブ抽出（採用）** | Draco は展開済み・ワールド行列も表示構成もそのまま得られる |

---

## 2. 動作環境

| 項目 | 要件 |
|---|---|
| OS | Windows（Chrome の探索にレジストリ / `%ProgramFiles%` を使用） |
| Python | 3.12 以降（開発・検証は 3.14.3） |
| パッケージ | `playwright` `numpy` `pyvista` `vtk` `PySide6` |
| ブラウザ | Chrome（アタッチ用）または Playwright 同梱 Chromium |

```powershell
pip install -r requirements.txt
python -m playwright install chromium   # 初回のみ
```

---

## 3. 使い方

### 3.1 GUI

```powershell
python -m sculpscanner
```

| 手順 | 操作 |
|---|---|
| 1 | URL 欄に 360°ビューのページを入力（既定はシビック） |
| 2 | **［Chrome を起動］** … デバッグポート付き Chrome が専用プロファイルで開く |
| 3 | その Chrome 上で車種・ボディカラー・グレード・オプションを自由に選ぶ |
| 4 | 読み込み完了後 **［接続してスキャン］** … 左ツリーにパーツ一覧が出る |
| 5 | 出力したいパーツにチェック（背景・壁・床・影は自動でオフ） |
| 6 | **［STL を作成］** … 右にプレビュー、下に三角形数・寸法・水密性が出る |
| 7 | **［STL を保存...］** |

- 単位・上方向・溶接・間引きを変えたら **［設定を反映して再構築］**（再スキャン不要）
- 手順 2 を飛ばした場合、［接続してスキャン］時に Playwright 同梱の Chromium が自動起動して URL を開く
- 埋め込みプレビューが使えない環境では **［別ウィンドウでプレビュー］**

> **注意**: 既に起動している通常の Chrome に、後からデバッグポートを開くことはできない。
> また実行中の Chrome と同じプロファイルは掴めないため、専用プロファイル
> （`%TEMP%\sculp-chrome-profile`）の別インスタンスとして起動する。

### 3.2 CLI

```powershell
# 自前で Chromium を起動して抽出
python -m sculpscanner.cli "https://www.honda.co.jp/CIVIC/configurator/" -o out\civic.stl

# 既にデバッグポート付きで開いてある Chrome から抽出
python -m sculpscanner.cli --no-launch -o out\vezel.stl

# パート一覧を確認するだけ
python -m sculpscanner.cli "https://www.honda.co.jp/FREED/configurator/" --list

# 抽出結果を再利用して出力設定だけ変える
python -m sculpscanner.cli --cache out\civic.npz -o out\civic_24.stl --scale 41.667
```

| オプション | 既定値 | 説明 |
|---|---|---|
| `url`（位置引数） | なし | 360°ビューのページ URL。アタッチ時は省略可 |
| `-o, --output` | `out.stl` | 出力する STL のパス |
| `--port` | `9222` | CDP ポート |
| `--no-attach` | off | 既存 Chrome を探さず必ず新規起動する |
| `--no-launch` | off | 既存 Chrome にアタッチできなければ失敗させる |
| `--scale` | `1000.0` | シーン単位への倍率（m→mm）。1/24 なら `41.667` |
| `--up` | `Z` | 出力の上方向軸。`Z`（3D プリント向け）/ `Y`（PlayCanvas のまま） |
| `--decimate` | `0.0` | 間引き率 0.0〜0.95 |
| `--no-weld` | off | 頂点の溶接をしない |
| `--include-background` | off | 背景・壁・床も出力に含める |
| `--list` | off | パート一覧を表示して終了 |
| `--keep-open` | off | 自前で起動したブラウザを閉じない |
| `--cache FILE` | なし | npz キャッシュ。存在すれば読込、無ければ抽出後に保存 |

### 3.3 Chrome の起動のみ

```powershell
launch_chrome_debug.bat "https://www.honda.co.jp/VEZEL/configurator/"
python -m sculpscanner.chrome "https://www.honda.co.jp/VEZEL/configurator/" 9222
```

---

## 4. アーキテクチャ

```
                Chrome / Chromium (PlayCanvas)
                ┌──────────────────────────────┐
                │ pc.Application.getApplication │
                │   app.root を深さ優先で走査   │
                │   render.meshInstances[]      │
                └───────────┬──────────────────┘
                            │ CDP (Playwright)
                            │ base64 1MiB チャンク
                            ▼
  browser.py  ── SculpSession: 接続 / 準備待ち / scan / fetch
       │
       ▼
  meshdata.py ── Part → ワールド変換 → 軸変換 → 単位変換
                       → 頂点溶接 → 任意で間引き → binary STL
       ▲
       │                    ┌── cli.py    （コマンドライン）
       └────────────────────┤
                            └── worker.py → gui.py + preview.py（GUI）
```

| ファイル | 役割 |
|---|---|
| `sculpscanner/browser.py` | Chrome への接続、準備待ち、パートのスキャンと取得 |
| `sculpscanner/extract_js.py` | ページに注入するシーングラフ走査 JavaScript |
| `sculpscanner/meshdata.py` | `Part` 定義、三角形の組み立て、座標変換、STL 出力、npz キャッシュ |
| `sculpscanner/chrome.py` | デバッグポート付き Chrome の探索と起動 |
| `sculpscanner/worker.py` | GUI 用ワーカースレッド（Playwright をこのスレッドに閉じ込める） |
| `sculpscanner/preview.py` | VTK による 3D プレビュー |
| `sculpscanner/gui.py` | PySide6 の画面 |
| `sculpscanner/cli.py` | コマンドライン入口 |

---

## 5. 接続仕様

### 5.1 接続の優先順位（`SculpSession.connect`）

1. `chromium.connect_over_cdp("http://127.0.0.1:<port>")` を **タイムアウト 3 秒**で試行
2. 全 context の全ページを走査し、PlayCanvas が動いているタブを候補にする
3. 候補の選択順:
   1. URL 引数と一致するタブ（クエリ文字列と末尾スラッシュを無視して比較）
   2. URL に `configurator` または `360` を含むタブ
   3. 候補の先頭
4. 候補が無ければ Playwright 同梱 Chromium を `headless=False` で起動し URL を開く
   - `--no-launch` 指定時はここで失敗させる

### 5.2 準備待ち（`SculpSession.wait_ready`）

| 項目 | 値 |
|---|---|
| 全体タイムアウト | 90 秒 |
| PlayCanvas 検出 | 全フレームに対し `window.pc && pc.Application.getApplication()` を 500ms 間隔で評価 |
| ロード完了判定 | `meshCount()` が **0 より大きく、かつ 3 回連続で同じ値**になったら完了 |
| ポーリング間隔 | 700ms |

メインフレームだけでなく**全フレームを探索**するため、iframe 内に埋め込まれていても動作する。

### 5.3 Chrome 起動時のフラグ（`chrome.py`）

```
--remote-debugging-port=<port>
--user-data-dir=%TEMP%\sculp-chrome-profile
--no-first-run  --no-default-browser-check  --disable-sync
--disable-search-engine-choice-screen  --disable-fre
```

`--no-first-run` 系が無いと、新規プロファイルでは初回セットアップ画面が出て
指定 URL が開かれない（実装時に実際に発生した問題）。

実行ファイルの探索順: レジストリ `App Paths\chrome.exe`（HKLM → HKCU）→
`%ProgramFiles%` / `%ProgramFiles(x86)%` / `%LocalAppData%` の Chrome → Edge。

---

## 6. 抽出仕様

### 6.1 走査規則

`app.root` から深さ優先で走査し、次の条件で meshInstance を収集する。

| 判定 | 内容 |
|---|---|
| エンティティ | `entity.enabled === false` の枝は**子孫ごとスキップ** |
| コンポーネント | `render`（新 API）と `model`（旧 API）の両方に対応。`enabled === false` は除外 |
| meshInstance | `visible === false` / `mesh` 無し は除外 |
| プリミティブ | `primitive[0].type !== PRIMITIVE_TRIANGLES` は除外（線分・点群を弾く） |
| ワールド変換 | `meshInstance.node.getWorldTransform().data`（列優先 16 要素） |

### 6.2 既定の除外規則

以下に該当するものは `excluded = true` としてスキャンされる（一覧には出るが既定で未チェック）。

| 種別 | 条件 |
|---|---|
| 名前 | エンティティのフルパスが `/background\|wall\|floor\|ground\|sphere\|panorama\|skybox\|shadow\|kage\|haikei/i` に一致 |
| レイヤー | 所属レイヤーが `Skybox(2)` `Immediate(3)` `UI(4)` `UI2(1002)` **のみ**で構成される |
| 大きさ | 世界空間 AABB の最大辺が **20 m 超**（背景ドームや床面を機械的に弾く） |

除外パターン・レイヤー・サイズ閾値は `extract_js.py` の
`DEFAULT_EXCLUDE_PATTERN` / `EXCLUDE_LAYERS` / `MAX_PART_SIZE_M` で変更できる。

### 6.3 頂点位置の読み出し

1. `Mesh#getPositions(arr)` を試す
2. 失敗時は `vertexBuffer` を直接読む
   - `format.elements` から `SEMANTIC_POSITION` の要素を探し、その `offset` / `stride` に従って
     `DataView.getFloat32(..., littleEndian=true)` で走査

### 6.4 インデックスの読み出し（重要）

> **PlayCanvas の `Mesh#getIndices(arr)` は「件数」を返す API であり、インデックス配列を返さない。**
> 戻り値をそのまま配列として扱うと `0,1,2,...` の連番フォールバックに落ち、
> 頂点数を超えるインデックスが生成される。実装初期にこれで**三角形の 74% を失っていた**。

現在は次の順で読む。

1. **`mesh.indexBuffer[0]` を直接読む（第一手段）**
   - `bytesPerIndex` から `Uint8Array` / `Uint16Array` / `Uint32Array` を選択
   - `bytesPerIndex` が無い場合は `INDEXFORMAT_*` から判定
2. `getIndices(arr)` 形式（配列を埋め件数を返す）
3. 配列を返す旧実装
4. 上記すべてが空のときのみ `primitive[0].base` から連番を生成

さらに `primitive[0].base` / `count` でスライスし、`count` は実際の長さを超えないようクランプする。

### 6.5 健全性チェック

- JS 側でパートごとに最大インデックス `maxIdx` を計算してメタに載せる
- `maxIdx >= verts` のパートがあれば `browser.py` が警告をログ出力
- Python 側 (`build_mesh`) でも範囲外インデックスの三角形を除去し、件数を警告表示

### 6.6 転送方式

| 項目 | 内容 |
|---|---|
| 経路 | CDP の `evaluate` 戻り値（base64 文字列） |
| 単位 | **1 MiB のバイナリチャンク**（`browser.CHUNK_BYTES`） |
| 対象 | 頂点配列（`Float32Array`）とインデックス配列（`Uint32Array`）を別々に取得 |
| 展開 | Python 側で `base64.b64decode` → `np.frombuffer` |
| 後始末 | 全取得後に `window.__SCULP__.release()` でページ側バッファを解放 |

ページ→ローカル HTTP サーバへの POST 方式は採らない。HTTPS ページから
`http://127.0.0.1` への通信は Private Network Access の制限を受ける可能性があるため。

### 6.7 ページ側 API（`window.__SCULP__`）

| 関数 | 戻り値 |
|---|---|
| `scan()` | パートのメタ情報配列（下表） |
| `chunk(id, kind, off, len)` | base64 文字列。`kind` は `'pos'` / `'idx'` |
| `meshCount()` | 有効な meshInstance の総数（ロード完了判定用） |
| `release()` | 抽出済みバッファを解放 |

`scan()` が返すメタ情報:

```
id, name, path, tris, verts, maxIdx, posBytes, idxBytes,
world[16](列優先), size[3](m), center[3], excluded, material
```

---

## 7. 座標・単位仕様

| 変換 | 内容 |
|---|---|
| ワールド変換 | 列優先 16 要素を **転置**して行優先 4×4 にし、`v @ M[:3,:3].T + M[:3,3]` |
| 軸変換（`up="Z"`） | PlayCanvas は Y-up 右手系。`(x, y, z) → (x, -z, y)` で Z-up に変換 |
| 軸変換（`up="Y"`） | 変換しない |
| 単位 | シーン単位はメートル。既定 `unit_scale=1000.0` で mm |

GUI の単位プリセット:

| 表示名 | 倍率 |
|---|---|
| 実寸 mm（×1000） | 1000.0 |
| 実寸 m（×1） | 1.0 |
| 1/24 スケール mm | 1000/24 ≒ 41.667 |
| 1/43 スケール mm | 1000/43 ≒ 23.256 |

---

## 8. メッシュ処理・出力仕様

`build_mesh(parts, up, unit_scale, weld, decimate)` の処理順:

1. `excluded` でなく頂点データを持つパートのみ対象
2. ワールド変換を適用
3. **範囲外インデックスの三角形を除去**（残すと `vtkCleanPolyData` がアクセス違反で落ちる）
4. 全パートを頂点オフセット補正して 1 つの配列に連結
5. 軸変換 → 単位倍率
6. `pyvista.PolyData` を生成
7. `weld=True` なら `clean(point_merging=True)` → `triangulate()`
8. `decimate > 0` なら `decimate_pro(rate, preserve_topology=True)`（上限 0.95）

出力:

| 項目 | 仕様 |
|---|---|
| 形式 | **binary STL**（`PolyData.save(path, binary=True)`） |
| 単位 | STL に単位情報は無い。既定は mm 相当の数値 |
| 統計 | 三角形数・頂点数・バウンディングボックス寸法・`is_manifold` |

### npz キャッシュ

`--cache` 指定時、`np.savez_compressed` で保存する。

| キー | 内容 |
|---|---|
| `meta` | 全パートのメタ情報を JSON 文字列化したもの |
| `p<id>` | パート `<id>` の頂点配列 |
| `i<id>` | パート `<id>` のインデックス配列 |

キャッシュがあればブラウザに接続せず、出力設定だけを変えて再出力できる。

---

## 9. GUI 仕様

### スレッド構成

Playwright の同期 API は**開始したスレッドでしか使えない**。
そのため `SessionThread`（`QThread`）の `run()` 内でセッションを生成・使用・破棄し、
GUI とはジョブキューと Qt シグナルでやり取りする。

| ジョブ | 処理 |
|---|---|
| `connect` | `connect` → `wait_ready` → `scan` |
| `fetch` | 選択パートの頂点・インデックス取得 |
| `quit` | ループ終了（`finally` で `session.close()`） |

| シグナル | 内容 |
|---|---|
| `log(str)` | ログ 1 行 |
| `connected(str)` | 接続したタブの URL |
| `scanned(object)` | `list[Part]`（頂点データなし） |
| `fetched(object)` | `list[Part]`（頂点データあり） |
| `progress(int, str)` | 進捗率とパーツ名 |
| `failed(str)` | エラーメッセージ |
| `busy(bool)` | 実行中の操作ロック |

### パーツツリー

- エンティティのフルパスを `/` で分解して階層表示
- 中間ノードは `ItemIsAutoTristate` で子のチェック状態を集約
- 葉に `PART_ID_ROLE` としてパート ID を保持
- ヘッダに選択件数と合計三角形数を常時表示
- ツールチップにフルパスと世界空間 AABB を表示

### プレビュー

- `vtkmodules.qt.QVTKRenderWindowInteractor` を埋め込む
- 環境に PyQt5 も存在するため、`vtkmodules.qt.PyQtImpl = "PySide6"` で実装を明示的に固定する
- 初回表示は斜め上からの視点（`ResetCamera` が視線方向と上方向を保つ性質を利用）
- 埋め込みに失敗した環境では別ウィンドウ（`pyvista.Plotter`）にフォールバック

---

## 10. 検証記録

| 車種 | 接続経路 | パート | 三角形 | 出力寸法 (mm) | 実車諸元 (mm) |
|---|---|---|---|---|---|
| CIVIC | 自前 Chromium 起動 | 307 / 312 | 323,587 | 4568.9 × 2080.5 × **1423.7** | 4550 × 1800 × **1415** |
| VEZEL | 既存 Chrome にアタッチ | 152 / 158 | 2,096,496 | 4387.9 × 2050.1 × **1582.4** | 4330 × 1790 × **1580** |

- 全高がミリ単位で一致しており、単位倍率と軸変換が正しいことを裏付けている
- 幅が諸元より広いのは**ドアミラーを含む全幅**のため
- 背景 5〜6 パートが自動除外されていることを一覧で確認済み
- 出力 STL を VTK で描画し、ホイール・ミラー・グリル等が正しい形状であることを目視確認済み

---

## 11. 制約と注意事項

| 項目 | 内容 |
|---|---|
| 水密性 | **manifold ではない。** 車両モデルは板状パーツの集合体。3D プリントするなら Blender / Meshmixer / PrusaSlicer 等での修復が必要 |
| 材質・色 | STL は形状のみを持つ。マテリアル名はメタ情報として取得しているが出力には含めない |
| 内装 | 内装メッシュもシーンに含まれる場合がある。不要ならツリーで外す |
| 対応範囲 | PlayCanvas 製ページに限る。連番画像方式の 360°ビューには対応しない |
| 追従性 | ページ側の実装（PlayCanvas のバージョンや命名）が変わると調整が必要。想定構造は `extract_js.py` の冒頭コメントに記載 |
| 権利 | 出力形状は **Honda の著作物**。個人的な検証・学習用途に留め、再配布・商用利用はしないこと |

---

## 12. トラブルシューティング

| 症状 | 対処 |
|---|---|
| 「PlayCanvas アプリを検出できませんでした」 | 360°ビューの読み込み完了を待ってから再実行。ローディング表示が消えているか確認 |
| 「接続先の Chrome が無いため、開く URL を指定してください」 | URL を入力するか、先に［Chrome を起動］を実行 |
| Chrome が起動するが URL が開かない | 初回セットアップ画面が出ていないか確認。`%TEMP%\sculp-chrome-profile` を削除して再試行 |
| アタッチできない | 通常の Chrome には後からポートを開けない。必ず［Chrome を起動］/ `launch_chrome_debug.bat` を使う |
| 「範囲外インデックスの三角形を除外しました」が大量に出る | ページ側の実装変更の可能性。`extract_js.py` の `readIndices` を確認 |
| 出力が巨大／重い | `--decimate 0.5` などで間引く。または不要パーツをツリーで外す |
| プレビューが出ない | ［別ウィンドウでプレビュー］を使う。埋め込み失敗の理由は画面に表示される |
