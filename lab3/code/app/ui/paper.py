"""实验二纸张原型的插画、手写标题与可操作图像按钮。"""

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QLabel, QPushButton, QSizePolicy

ART = Path(__file__).with_name("art") / "paper"


@lru_cache(maxsize=64)
def artwork(name):
    """缓存原图分辨率，在高分屏按当前控件尺寸绘制。"""
    return QPixmap(str(ART / f"{name}.png"))


def _paint(widget, name, align, interactive=False):
    image = artwork(name)
    painter = QPainter(widget)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    rect = widget.rect().adjusted(1, 1, -1, -1)
    scale = min(rect.width() / image.width(), rect.height() / image.height())
    width, height = image.width() * scale, image.height() * scale
    left = (rect.width() - width) / 2 if align == "center" else 0
    target = QRectF(left + 1, 1 + (rect.height() - height) / 2, width, height)
    painter.setOpacity(1 if widget.isEnabled() else .42)
    painter.drawPixmap(target, image, QRectF(image.rect()))
    if interactive and (widget.hasFocus() or widget.isDown()):
        painter.setPen(QPen(QColor("#77acd3"), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, 8, 8)
    painter.end()


class Illustration(QLabel):
    """用可访问的文字标签承载原稿静态图层。"""

    def __init__(self, name, height, text="", *, width=None, align="left"):
        super().__init__(text)
        self.asset = name
        self.align = align
        self.setAccessibleName(text)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(height)
        if width:
            self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            self.setFixedWidth(width)

    def paintEvent(self, event):
        del event
        _paint(self, self.asset, self.align)


class PictureButton(QPushButton):
    """保留鼠标、键盘、读屏和禁用语义的原稿图标按钮。"""

    def __init__(self, text, asset, height, action):
        super().__init__(text)
        self.asset = asset
        self.setFixedHeight(height)
        self.setStyleSheet(f"min-height:{height}px; max-height:{height}px; padding:0; border:none;")
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(text)
        self.setToolTip(text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(lambda _: action())  # pylint: disable=no-member

    def paintEvent(self, event):
        del event
        _paint(self, self.asset, "center", interactive=True)
