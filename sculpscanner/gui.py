"""PySide6 の GUI。

流れ:
    URL 入力 → [接続してスキャン] → パート一覧が出る
             → 出力したいパートにチェック → [STL を作成] → プレビュー
             → [STL を保存...]
"""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .browser import DEFAULT_CDP_PORT
from .chrome import launch as launch_chrome
from .meshdata import build_mesh, format_stats, save_stl, stats
from .preview import PreviewWidget
from .worker import SessionThread

SAMPLE_URL = "https://www.honda.co.jp/CIVIC/configurator/"

# 単位プリセット: 表示名 -> シーン単位(m)への倍率
SCALE_PRESETS = {
    "実寸 mm（×1000）": 1000.0,
    "実寸 m（×1）": 1.0,
    "1/24 スケール mm": 1000.0 / 24.0,
    "1/43 スケール mm": 1000.0 / 43.0,
}

PART_ID_ROLE = Qt.UserRole + 1


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sculp-Scanner — 360°ビューから STL")
        self.resize(1280, 800)

        self._parts = []
        self._mesh = None

        self.thread = SessionThread(self)
        self.thread.log.connect(self._log)
        self.thread.connected.connect(lambda u: self._log(f"接続: {u}"))
        self.thread.scanned.connect(self._on_scanned)
        self.thread.fetched.connect(self._on_fetched)
        self.thread.progress.connect(self._on_progress)
        self.thread.failed.connect(self._on_failed)
        self.thread.busy.connect(self._on_busy)
        self.thread.start()

        self._build_ui()

    # --- UI 構築 --------------------------------------------------------

    def _build_ui(self):
        root = QWidget()
        outer = QVBoxLayout(root)

        # 接続行
        conn = QHBoxLayout()
        conn.addWidget(QLabel("URL:"))
        self.url_edit = QLineEdit(SAMPLE_URL)
        self.url_edit.setPlaceholderText("360°ビューのページ URL")
        conn.addWidget(self.url_edit, 1)

        self.attach_check = QCheckBox("既存 Chrome に接続")
        self.attach_check.setChecked(True)
        self.attach_check.setToolTip(
            "launch_chrome_debug.bat で起動した Chrome があればそのタブを使います。\n"
            "見つからない場合は Playwright の Chromium を自動で起動します。"
        )
        conn.addWidget(self.attach_check)

        conn.addWidget(QLabel("ポート:"))
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(DEFAULT_CDP_PORT)
        conn.addWidget(self.port_spin)

        self.chrome_btn = QPushButton("Chrome を起動")
        self.chrome_btn.setToolTip(
            "デバッグポート付きの Chrome を専用プロファイルで起動し、URL を開きます。\n"
            "既に開いている通常の Chrome には後からポートを開けないため、こちらを使ってください。"
        )
        self.chrome_btn.clicked.connect(self._do_launch_chrome)
        conn.addWidget(self.chrome_btn)

        self.connect_btn = QPushButton("接続してスキャン")
        self.connect_btn.clicked.connect(self._do_connect)
        conn.addWidget(self.connect_btn)
        outer.addLayout(conn)

        # 中央: パート一覧 / プレビュー
        splitter = QSplitter(Qt.Horizontal)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(QLabel("出力するパート"))

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["パーツ", "三角形"])
        self.tree.setColumnWidth(0, 340)
        self.tree.itemChanged.connect(self._on_item_changed)
        lv.addWidget(self.tree, 1)

        sel = QHBoxLayout()
        for text, fn in (("すべて選択", lambda: self._set_all(True)),
                         ("すべて解除", lambda: self._set_all(False))):
            b = QPushButton(text)
            b.clicked.connect(fn)
            sel.addWidget(b)
        lv.addLayout(sel)
        splitter.addWidget(left)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self.preview = PreviewWidget()
        rv.addWidget(self.preview, 1)
        self.stats_label = QLabel("—")
        self.stats_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        rv.addWidget(self.stats_label)
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([420, 860])
        outer.addWidget(splitter, 1)

        # 出力設定
        box = QGroupBox("出力設定")
        opt = QHBoxLayout(box)

        opt.addWidget(QLabel("単位:"))
        self.scale_combo = QComboBox()
        self.scale_combo.addItems(SCALE_PRESETS.keys())
        opt.addWidget(self.scale_combo)

        opt.addWidget(QLabel("上方向:"))
        self.up_combo = QComboBox()
        self.up_combo.addItems(["Z（3D プリント向け）", "Y（元のまま）"])
        opt.addWidget(self.up_combo)

        self.weld_check = QCheckBox("頂点を溶接")
        self.weld_check.setChecked(True)
        opt.addWidget(self.weld_check)

        opt.addWidget(QLabel("間引き:"))
        self.decimate_spin = QDoubleSpinBox()
        self.decimate_spin.setRange(0.0, 0.95)
        self.decimate_spin.setSingleStep(0.05)
        self.decimate_spin.setDecimals(2)
        opt.addWidget(self.decimate_spin)

        opt.addStretch(1)
        self.build_btn = QPushButton("STL を作成")
        self.build_btn.clicked.connect(self._do_build)
        self.build_btn.setEnabled(False)
        opt.addWidget(self.build_btn)

        self.rebuild_btn = QPushButton("設定を反映して再構築")
        self.rebuild_btn.clicked.connect(self._rebuild)
        self.rebuild_btn.setEnabled(False)
        opt.addWidget(self.rebuild_btn)

        self.ext_btn = QPushButton("別ウィンドウでプレビュー")
        self.ext_btn.clicked.connect(self.preview.show_external)
        self.ext_btn.setEnabled(False)
        opt.addWidget(self.ext_btn)

        self.save_btn = QPushButton("STL を保存...")
        self.save_btn.clicked.connect(self._do_save)
        self.save_btn.setEnabled(False)
        opt.addWidget(self.save_btn)
        outer.addWidget(box)

        # 進捗とログ
        self.progress = QProgressBar()
        outer.addWidget(self.progress)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(140)
        outer.addWidget(self.log_view)

        self.setCentralWidget(root)
        self.preview.start()
        self._log("URL を確認して［接続してスキャン］を押してください。")

    # --- ジョブ ---------------------------------------------------------

    def _do_launch_chrome(self):
        url = self.url_edit.text().strip() or SAMPLE_URL
        try:
            exe = launch_chrome(url, self.port_spin.value())
        except Exception as exc:
            QMessageBox.critical(self, "Chrome を起動できません", str(exc))
            return
        self._log(f"Chrome を起動しました: {exe}")
        self._log("360°ビューの読み込みが終わったら［接続してスキャン］を押してください。")

    def _do_connect(self):
        self.tree.clear()
        self._parts = []
        self.build_btn.setEnabled(False)
        self.progress.setValue(0)
        self.thread.connect_and_scan(
            self.url_edit.text().strip(),
            self.port_spin.value(),
            self.attach_check.isChecked(),
        )

    def _do_build(self):
        selected = self._selected_ids()
        if not selected:
            QMessageBox.warning(self, "Sculp-Scanner", "出力するパートを 1 つ以上選んでください。")
            return
        for p in self._parts:
            p.excluded = p.id not in selected
        self.progress.setValue(0)
        self.thread.fetch(self._parts)

    def _rebuild(self):
        selected = self._selected_ids()
        for p in self._parts:
            p.excluded = p.id not in selected
        missing = [p for p in self._parts if not p.excluded and not p.loaded]
        if missing:
            # 未取得のパートが増えたのでブラウザから取り直す
            self.thread.fetch(self._parts)
        else:
            self._assemble()

    def _do_save(self):
        if self._mesh is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "STL を保存", os.path.abspath("model.stl"), "STL ファイル (*.stl)"
        )
        if not path:
            return
        try:
            save_stl(self._mesh, path)
        except Exception as exc:
            QMessageBox.critical(self, "保存に失敗しました", str(exc))
            return
        self._log(f"保存しました: {path}")
        QMessageBox.information(self, "Sculp-Scanner", f"保存しました:\n{path}")

    # --- スレッドからの通知 ---------------------------------------------

    def _on_scanned(self, parts):
        self._parts = parts
        self._populate_tree(parts)
        self.build_btn.setEnabled(True)
        self._log("パートを選んで［STL を作成］を押してください。")

    def _on_fetched(self, parts):
        self._parts = parts
        self._assemble()

    def _on_progress(self, pct, name):
        self.progress.setValue(pct)
        self.progress.setFormat(f"%p%  {name}")

    def _on_failed(self, msg):
        self._log(f"エラー: {msg}")
        QMessageBox.critical(self, "Sculp-Scanner", msg)

    def _on_busy(self, busy):
        """ジョブ実行中は操作を止め、終了時は現在の状態に応じて復帰させる。"""
        if busy:
            for w in (self.connect_btn, self.build_btn, self.rebuild_btn,
                      self.save_btn, self.ext_btn, self.tree):
                w.setEnabled(False)
            return

        has_mesh = self._mesh is not None
        self.connect_btn.setEnabled(True)
        self.tree.setEnabled(True)
        self.build_btn.setEnabled(bool(self._parts))
        self.rebuild_btn.setEnabled(has_mesh)
        self.save_btn.setEnabled(has_mesh)
        self.ext_btn.setEnabled(has_mesh)

    # --- メッシュ組み立て ------------------------------------------------

    def _assemble(self):
        try:
            self._mesh = build_mesh(
                self._parts,
                up="Z" if self.up_combo.currentIndex() == 0 else "Y",
                unit_scale=SCALE_PRESETS[self.scale_combo.currentText()],
                weld=self.weld_check.isChecked(),
                decimate=self.decimate_spin.value(),
            )
        except Exception as exc:
            self._on_failed(str(exc))
            return

        st = stats(self._mesh)
        self.stats_label.setText(format_stats(st).replace("\n", "　|　"))
        self._log(format_stats(st).replace("\n", " / "))
        self.preview.show_mesh(self._mesh)
        self.save_btn.setEnabled(True)
        self.rebuild_btn.setEnabled(True)
        self.ext_btn.setEnabled(True)

    # --- ツリー ---------------------------------------------------------

    def _populate_tree(self, parts):
        self.tree.blockSignals(True)
        self.tree.clear()
        nodes: dict[str, QTreeWidgetItem] = {}

        def ensure(path: str) -> QTreeWidgetItem | None:
            if not path:
                return None
            if path in nodes:
                return nodes[path]
            head, _, tail = path.rpartition("/")
            parent = ensure(head)
            item = QTreeWidgetItem(parent or self.tree, [tail, ""])
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
            item.setCheckState(0, Qt.Unchecked)
            nodes[path] = item
            return item

        for p in parts:
            # 末尾（メッシュ名）は葉として作るので、親までを ensure する
            parent_path = p.path.rpartition("/")[0]
            parent = ensure(parent_path)
            leaf = QTreeWidgetItem(parent or self.tree, [p.name, f"{p.tris:,}"])
            leaf.setFlags(leaf.flags() | Qt.ItemIsUserCheckable)
            leaf.setData(0, PART_ID_ROLE, p.id)
            leaf.setCheckState(0, Qt.Unchecked if p.excluded else Qt.Checked)
            if p.size:
                leaf.setToolTip(0, f"{p.path}\nAABB: {p.size[0]:.2f} x {p.size[1]:.2f} x {p.size[2]:.2f} m")

        self.tree.blockSignals(False)
        self.tree.expandToDepth(1)
        self._update_counts()

    def _iter_leaves(self):
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            it = stack.pop()
            if it is None:
                continue
            for i in range(it.childCount()):
                stack.append(it.child(i))
            if it.data(0, PART_ID_ROLE) is not None:
                yield it

    def _selected_ids(self) -> set[int]:
        return {
            it.data(0, PART_ID_ROLE)
            for it in self._iter_leaves()
            if it.checkState(0) == Qt.Checked
        }

    def _set_all(self, checked: bool):
        self.tree.blockSignals(True)
        for it in self._iter_leaves():
            it.setCheckState(0, Qt.Checked if checked else Qt.Unchecked)
        self.tree.blockSignals(False)
        self._update_counts()

    def _on_item_changed(self, *_):
        self._update_counts()

    def _update_counts(self):
        ids = self._selected_ids()
        tris = sum(p.tris for p in self._parts if p.id in ids)
        self.tree.setHeaderLabels([f"パーツ（選択 {len(ids)}）", f"{tris:,} tri"])

    # --- その他 ---------------------------------------------------------

    def _log(self, msg: str):
        self.log_view.appendPlainText(msg)

    def closeEvent(self, event):
        self.preview.close_vtk()
        self.thread.shutdown()
        self.thread.wait(5000)
        super().closeEvent(event)


def main(argv=None) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
