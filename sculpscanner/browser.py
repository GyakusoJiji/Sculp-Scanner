"""Chrome への接続と、ページからのメッシュ抽出。

接続は 2 経路:
  1. 既に --remote-debugging-port=9222 付きで起動している Chrome に CDP でアタッチ
  2. 見つからなければ Playwright 同梱の Chromium を起動して URL を開く

Playwright の同期 API はそれを開始したスレッドでのみ使える。
GUI から使う場合は worker.py の専用スレッド経由で呼ぶこと。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from playwright.sync_api import sync_playwright

from .extract_js import HAS_PC_JS, install_js
from .meshdata import Part, decode_indices, decode_positions, part_from_meta

DEFAULT_CDP_PORT = 9222
CHUNK_BYTES = 1 << 20  # 1MiB ごとに base64 で吸い上げる


@dataclass
class PageInfo:
    index: int
    title: str
    url: str


class SculpSession:
    """1 回分の抽出セッション。使い終わったら close() すること。"""

    def __init__(self, log=None):
        self._log = log or (lambda msg: None)
        self._pw = None
        self._browser = None
        self._page = None
        self._frame = None
        self._owns_browser = False
        self._meta: list[dict] = []

    # --- 接続 -----------------------------------------------------------

    def connect(self, url: str | None = None, *, port: int = DEFAULT_CDP_PORT,
                allow_launch: bool = True, prefer_attach: bool = True):
        """Chrome に接続し、PlayCanvas が動いているフレームを掴む。"""
        if self._pw is None:
            self._pw = sync_playwright().start()

        page = None
        if prefer_attach:
            page = self._try_attach(url, port)

        if page is None:
            if not allow_launch:
                raise RuntimeError(
                    f"127.0.0.1:{port} で待ち受けている Chrome が見つかりませんでした。\n"
                    "launch_chrome_debug.bat から Chrome を起動してください。"
                )
            if not url:
                raise RuntimeError("接続先の Chrome が無いため、開く URL を指定してください。")
            page = self._launch(url)

        self._page = page
        self._log(f"対象タブ: {page.url}")
        return page

    def _try_attach(self, url: str | None, port: int):
        endpoint = f"http://127.0.0.1:{port}"
        try:
            browser = self._pw.chromium.connect_over_cdp(endpoint, timeout=3000)
        except Exception as exc:
            self._log(f"既存 Chrome へのアタッチ不可 ({exc.__class__.__name__})。新規に起動します。")
            return None

        self._browser = browser
        self._owns_browser = False
        pages = [p for ctx in browser.contexts for p in ctx.pages]
        self._log(f"アタッチ成功。タブ {len(pages)} 個を検査します。")

        candidates = []
        for p in pages:
            try:
                if self._page_has_pc(p):
                    candidates.append(p)
            except Exception:
                continue

        if not candidates:
            self._log("PlayCanvas ページを開いているタブが見つかりませんでした。")
            return None

        if url:
            for p in candidates:
                if _same_target(p.url, url):
                    return p
        for p in candidates:
            if "configurator" in p.url or "360" in p.url:
                return p
        return candidates[0]

    @staticmethod
    def _page_has_pc(page) -> bool:
        for fr in page.frames:
            try:
                if fr.evaluate(HAS_PC_JS):
                    return True
            except Exception:
                continue
        return False

    def _launch(self, url: str):
        self._log("Playwright の Chromium を起動します...")
        self._browser = self._pw.chromium.launch(headless=False, args=["--start-maximized"])
        self._owns_browser = True
        ctx = self._browser.new_context(viewport=None)
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=120000)
        return page

    def list_pages(self) -> list[PageInfo]:
        if self._browser is None:
            return []
        pages = [p for ctx in self._browser.contexts for p in ctx.pages]
        return [PageInfo(i, p.title(), p.url) for i, p in enumerate(pages)]

    # --- 準備待ち -------------------------------------------------------

    def wait_ready(self, timeout: float = 90.0, settle_polls: int = 3) -> int:
        """PlayCanvas が起動し、メッシュのロードが落ち着くまで待つ。"""
        if self._page is None:
            raise RuntimeError("先に connect() を呼んでください。")

        deadline = time.time() + timeout
        frame = None
        while time.time() < deadline:
            for fr in self._page.frames:
                try:
                    if fr.evaluate(HAS_PC_JS):
                        frame = fr
                        break
                except Exception:
                    continue
            if frame is not None:
                break
            self._page.wait_for_timeout(500)

        if frame is None:
            raise RuntimeError(
                "PlayCanvas アプリを検出できませんでした。\n"
                "360°ビューのページが表示され、読み込みが完了しているか確認してください。"
            )

        self._frame = frame
        frame.evaluate(install_js())
        self._log("抽出スクリプトを注入しました。モデルの読み込みを待っています...")

        last, stable, count = -1, 0, 0
        while time.time() < deadline:
            count = frame.evaluate("() => window.__SCULP__.meshCount()")
            if count > 0 and count == last:
                stable += 1
                if stable >= settle_polls:
                    break
            else:
                stable = 0
            last = count
            self._page.wait_for_timeout(700)

        if count <= 0:
            raise RuntimeError("メッシュが 1 つも見つかりませんでした。読み込み完了後に再試行してください。")

        self._log(f"メッシュインスタンス {count} 個を検出しました。")
        return count

    # --- 抽出 -----------------------------------------------------------

    def scan(self) -> list[Part]:
        """シーングラフを走査してパート一覧（頂点データ抜き）を得る。"""
        if self._frame is None:
            self.wait_ready()
        # 再注入（ページ遷移や色替えで失われている場合に備える）
        if not self._frame.evaluate("() => !!window.__SCULP__"):
            self._frame.evaluate(install_js())

        self._meta = self._frame.evaluate("() => window.__SCULP__.scan()")
        parts = [part_from_meta(m) for m in self._meta]
        keep = sum(1 for p in parts if not p.excluded)
        tris = sum(p.tris for p in parts if not p.excluded)
        self._log(f"パート {len(parts)} 個（採用 {keep} 個 / 三角形 {tris:,}）")

        broken = [m for m in self._meta if m.get("maxIdx", 0) >= m["verts"]]
        if broken:
            self._log(
                f"[警告] インデックスが頂点数を超えるパートが {len(broken)} 個あります"
                f"（例: {broken[0]['name']}）。抽出時に該当三角形は除外されます。"
            )
        return parts

    def fetch(self, parts: list[Part], progress=None) -> list[Part]:
        """excluded でないパートの頂点・インデックスを取得して Part に埋める。"""
        if self._frame is None:
            raise RuntimeError("先に scan() を呼んでください。")

        targets = [p for p in parts if not p.excluded]
        meta_by_id = {m["id"]: m for m in self._meta}
        total = sum(meta_by_id[p.id]["posBytes"] + meta_by_id[p.id]["idxBytes"] for p in targets)
        done = 0

        for p in targets:
            m = meta_by_id[p.id]
            pos_b64 = self._pull(p.id, "pos", m["posBytes"], lambda n: None)
            done += m["posBytes"]
            if progress:
                progress(done, total, p.name)
            idx_b64 = self._pull(p.id, "idx", m["idxBytes"], lambda n: None)
            done += m["idxBytes"]
            if progress:
                progress(done, total, p.name)

            p.positions = decode_positions(pos_b64)
            p.indices = decode_indices(idx_b64)

        self._frame.evaluate("() => window.__SCULP__.release()")
        return parts

    def _pull(self, part_id: int, kind: str, nbytes: int, _cb) -> list[str]:
        out: list[str] = []
        off = 0
        while off < nbytes:
            s = self._frame.evaluate(
                "(a) => window.__SCULP__.chunk(a[0], a[1], a[2], a[3])",
                [part_id, kind, off, CHUNK_BYTES],
            )
            if not s:
                break
            out.append(s)
            off += CHUNK_BYTES
        return out

    # --- 後始末 ---------------------------------------------------------

    def close(self):
        try:
            if self._browser is not None and self._owns_browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass
        self._pw = self._browser = self._page = self._frame = None


def _same_target(page_url: str, wanted: str) -> bool:
    a = page_url.split("?")[0].rstrip("/").lower()
    b = wanted.split("?")[0].rstrip("/").lower()
    return a == b or a.endswith(b) or b.endswith(a)
