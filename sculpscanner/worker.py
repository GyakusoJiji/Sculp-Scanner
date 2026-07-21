"""GUI から Playwright を使うためのワーカースレッド。

Playwright の同期 API は「開始したスレッド」でしか使えないため、
セッションはこのスレッドの中だけで生成・使用・破棄する。
GUI 側とはジョブキューと Qt シグナルでやり取りする。
"""

from __future__ import annotations

import queue

from PySide6.QtCore import QThread, Signal

from .browser import SculpSession


class SessionThread(QThread):
    log = Signal(str)
    connected = Signal(str)        # 接続できたタブの URL
    scanned = Signal(object)       # list[Part]（頂点データ無し）
    fetched = Signal(object)       # list[Part]（頂点データ入り）
    progress = Signal(int, str)    # 0-100, ラベル
    failed = Signal(str)
    busy = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._jobs: queue.Queue = queue.Queue()

    # --- GUI スレッドから呼ぶ ------------------------------------------

    def connect_and_scan(self, url: str, port: int, prefer_attach: bool):
        self._jobs.put(("connect", {"url": url, "port": port, "prefer_attach": prefer_attach}))

    def fetch(self, parts):
        self._jobs.put(("fetch", {"parts": parts}))

    def shutdown(self):
        self._jobs.put(("quit", {}))

    # --- ワーカースレッド本体 ------------------------------------------

    def run(self):
        session = SculpSession(log=self.log.emit)
        try:
            while True:
                kind, kw = self._jobs.get()
                if kind == "quit":
                    break

                self.busy.emit(True)
                try:
                    if kind == "connect":
                        page = session.connect(
                            kw["url"] or None,
                            port=kw["port"],
                            prefer_attach=kw["prefer_attach"],
                        )
                        session.wait_ready()
                        self.connected.emit(page.url)
                        self.scanned.emit(session.scan())

                    elif kind == "fetch":
                        parts = kw["parts"]
                        session.fetch(parts, progress=self._on_progress)
                        self.progress.emit(100, "完了")
                        self.fetched.emit(parts)

                except Exception as exc:
                    self.failed.emit(f"{exc}")
                finally:
                    self.busy.emit(False)
        finally:
            session.close()

    def _on_progress(self, done: int, total: int, name: str):
        self.progress.emit(int(100 * done / max(total, 1)), name)
