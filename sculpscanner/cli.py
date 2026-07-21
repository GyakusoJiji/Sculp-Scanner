"""コマンドライン入口。

    python -m sculpscanner.cli https://www.honda.co.jp/CIVIC/configurator/ -o out/civic.stl
"""

from __future__ import annotations

import argparse
import os
import sys

from .browser import DEFAULT_CDP_PORT, SculpSession
from .meshdata import build_mesh, format_stats, load_cache, save_cache, save_stl, stats


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sculpscanner",
        description="Chrome で開いている 360°ビュー（PlayCanvas）から 3D を抽出し STL 保存する",
    )
    p.add_argument("url", nargs="?", help="360°ビューのページ URL")
    p.add_argument("-o", "--output", default="out.stl", help="出力する STL のパス")
    p.add_argument("--port", type=int, default=DEFAULT_CDP_PORT, help="CDP ポート")
    p.add_argument("--no-attach", action="store_true", help="既存 Chrome にアタッチせず必ず新規起動する")
    p.add_argument("--no-launch", action="store_true", help="既存 Chrome にアタッチできない場合に失敗させる")
    p.add_argument("--scale", type=float, default=1000.0, help="シーン単位への倍率（既定 1000 = m→mm）")
    p.add_argument("--up", choices=["Z", "Y"], default="Z", help="出力の上方向軸")
    p.add_argument("--decimate", type=float, default=0.0, help="間引き率 0.0-0.95")
    p.add_argument("--no-weld", action="store_true", help="重複頂点の溶接をしない")
    p.add_argument("--include-background", action="store_true", help="背景・壁・床も出力に含める")
    p.add_argument("--list", action="store_true", help="パート一覧を表示するだけで終了する")
    p.add_argument("--keep-open", action="store_true", help="自前で起動したブラウザを閉じない")
    p.add_argument("--cache", help="抽出結果の npz キャッシュ。存在すれば読み込み、無ければ抽出後に保存する")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    def log(msg):
        print(msg, flush=True)

    if args.cache and os.path.exists(args.cache):
        log(f"キャッシュを読み込みます: {args.cache}")
        parts = load_cache(args.cache)
        return _finish(parts, args)

    session = SculpSession(log=log)
    try:
        session.connect(
            args.url,
            port=args.port,
            allow_launch=not args.no_launch,
            prefer_attach=not args.no_attach,
        )
        session.wait_ready()
        parts = session.scan()

        if args.include_background:
            for p in parts:
                p.excluded = False

        print("\n--- パート一覧 ---")
        for p in parts:
            mark = "  " if p.excluded else "* "
            size = f" {p.size[0]:.2f}x{p.size[1]:.2f}x{p.size[2]:.2f}m" if p.size else ""
            print(f"{mark}[{p.id:3d}] {p.tris:8,} tri  {p.path}{size}")
        print("(* = 出力対象)\n")

        if args.list:
            return 0

        def progress(done, total, name):
            pct = 100.0 * done / max(total, 1)
            print(f"\r転送中 {pct:5.1f}%  {name[:40]:40s}", end="", flush=True)

        session.fetch(parts, progress=progress)
        print()

    finally:
        if not args.keep_open:
            session.close()

    if args.cache:
        os.makedirs(os.path.dirname(os.path.abspath(args.cache)) or ".", exist_ok=True)
        save_cache(parts, args.cache)
        print(f"キャッシュを保存しました: {args.cache}")

    return _finish(parts, args)


def _finish(parts, args) -> int:
    mesh = build_mesh(
        parts,
        up=args.up,
        unit_scale=args.scale,
        weld=not args.no_weld,
        decimate=args.decimate,
    )
    st = stats(mesh)
    print("\n--- 結果 ---")
    print(format_stats(st))

    out = os.path.abspath(args.output)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    save_stl(mesh, out)
    print(f"\n保存しました: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
