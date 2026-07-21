"""メッシュプレビュー。

VTK 付属の QVTKRenderWindowInteractor を PySide6 で埋め込む。
（環境に PyQt5 も入っているため、実装を明示的に PySide6 に固定する。）
利用できない場合は別ウィンドウ表示にフォールバックする。
"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

_EMBED_ERROR = None

try:
    import vtkmodules.qt

    vtkmodules.qt.PyQtImpl = "PySide6"  # 自動判定が PyQt5 を選ぶのを防ぐ

    import vtkmodules.vtkInteractionStyle  # noqa: F401  （対話スタイルの登録）
    import vtkmodules.vtkRenderingOpenGL2  # noqa: F401  （描画バックエンドの登録）
    from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor
    from vtkmodules.vtkRenderingAnnotation import vtkAxesActor
    from vtkmodules.vtkRenderingCore import vtkActor, vtkPolyDataMapper, vtkRenderer

    _EMBED_OK = True
except Exception as exc:  # pragma: no cover - 環境依存
    _EMBED_OK = False
    _EMBED_ERROR = exc


class PreviewWidget(QWidget):
    """PolyData を 1 つだけ表示する軽量ビューア。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._actor = None
        self._mesh = None

        if _EMBED_OK:
            self._vtk = QVTKRenderWindowInteractor(self)
            self._renderer = vtkRenderer()
            self._renderer.SetBackground(0.16, 0.17, 0.20)
            self._vtk.GetRenderWindow().AddRenderer(self._renderer)
            self._axes = vtkAxesActor()
            layout.addWidget(self._vtk)
        else:
            self._vtk = None
            layout.addWidget(
                QLabel(
                    "3D プレビューを埋め込めませんでした。\n"
                    "「別ウィンドウでプレビュー」から表示してください。\n"
                    f"({_EMBED_ERROR})"
                )
            )

    def start(self):
        if self._vtk is not None:
            self._vtk.Initialize()
            self._vtk.Start()

    @property
    def embedded(self) -> bool:
        return self._vtk is not None

    def show_mesh(self, mesh):
        """pyvista.PolyData（= vtkPolyData）を表示する。"""
        self._mesh = mesh
        if self._vtk is None:
            return
        if self._actor is not None:
            self._renderer.RemoveActor(self._actor)

        mapper = vtkPolyDataMapper()
        mapper.SetInputData(mesh)
        mapper.ScalarVisibilityOff()

        actor = vtkActor()
        actor.SetMapper(mapper)
        actor.GetProperty().SetColor(0.72, 0.78, 0.88)
        actor.GetProperty().SetInterpolationToPhong()

        self._renderer.AddActor(actor)
        self._actor = actor

        # ResetCamera は視線方向と上方向を保つので、先に斜め上からの向きを与える
        cam = self._renderer.GetActiveCamera()
        cam.SetFocalPoint(0.0, 0.0, 0.0)
        cam.SetPosition(1.0, -1.4, 0.6)
        cam.SetViewUp(0.0, 0.0, 1.0)
        self._renderer.ResetCamera()
        self._vtk.GetRenderWindow().Render()

    def show_external(self):
        """埋め込みが使えない／もっと大きく見たいとき用の別ウィンドウ表示。"""
        if self._mesh is None:
            return
        import pyvista as pv

        p = pv.Plotter()
        p.add_mesh(self._mesh, color="lightsteelblue", smooth_shading=True)
        p.show_axes()
        p.show()

    def close_vtk(self):
        if self._vtk is not None:
            self._vtk.GetRenderWindow().Finalize()
