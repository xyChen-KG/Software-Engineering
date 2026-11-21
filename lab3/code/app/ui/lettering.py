"""手账中的动态标题和金额，直接排版业务文字并保留读屏内容。"""

from math import ceil

from PySide6.QtCore import QPointF, QSize, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtGui import QTextLayout, QTextOption
from PySide6.QtWidgets import QLabel, QSizePolicy, QStyle


def wrapped_layout(text, font, width):
    """按实际可用宽度排版，连续数字和没有空格的长中文都可换行。"""
    layout = QTextLayout(text, font)
    options = QTextOption()
    options.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(options)
    layout.beginLayout()
    height = 0
    while (line := layout.createLine()).isValid():
        line.setLineWidth(width)
        line.setPosition(QPointF(2, height))
        height += line.height()
    layout.endLayout()
    return layout


class InkLabel(QLabel):
    """金额随可用宽度缩放，长标题自然换行。色笔下划线不遮挡文字。"""

    SIZES = {"bookTitle": 30, "coverTitle": 23, "amount": 23, "heroAmount": 29}

    def __init__(self, text, style):
        super().__init__(text)
        self.title = style in {"bookTitle", "coverTitle"}
        self.pixels = self.SIZES[style]
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setAccessibleName(text)
        self.setToolTip(text)
        self.setObjectName(style)
        policy = QSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.stroke = None
        if self.title:
            self.stroke = QVariantAnimation(self)
            self.stroke.setDuration(230)
            self.stroke.setStartValue(0.0)
            self.stroke.setEndValue(1.0)
            self.stroke.valueChanged.connect(lambda _: self.update())  # pylint: disable=no-member

    def _lines(self, width):
        width = max(1, width - 4)
        font = QFont(self.font())
        families = ["Kaiti SC", "STKaiti", "KaiTi", "PingFang SC"] if self.title else [
            "Noteworthy", "Chalkboard SE", "Kaiti SC", "DejaVu Sans"]
        font.setFamilies(families)
        font.setBold(True)
        font.setPixelSize(self.pixels)
        if not self.title:
            advance = QFontMetricsF(font).horizontalAdvance(self.text())
            if advance > width:
                font.setPixelSize(max(14, int(self.pixels * width / advance)))
        layout = wrapped_layout(self.text(), font, width)
        if self.title and layout.lineCount() > 2:
            # Qt 的字符串位置是 UTF-16 索引，表情或扩展汉字可能占两个单元。
            end = 2 * layout.lineAt(1).textStart()
            encoded = self.text().encode("utf-16-le")
            prefix = encoded[:end].decode("utf-16-le")
            tail = QFontMetricsF(font).elidedText(encoded[end:].decode("utf-16-le"),
                                                 Qt.TextElideMode.ElideRight, width)
            layout = wrapped_layout(prefix + tail, font, width)
        return layout, ceil(layout.boundingRect().height()) + (7 if self.title else 2)

    def minimumSizeHint(self):
        return QSize(0, self.heightForWidth(400))

    def sizeHint(self):
        return QSize(110, self.minimumSizeHint().height())

    def heightForWidth(self, width):
        return self._lines(width)[1]

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 嵌套横排卡片的最小高度须按分配后的真实宽度重新计算。
        needed = self.heightForWidth(event.size().width())
        if self.minimumHeight() != needed:
            self.setMinimumHeight(needed)

    def showEvent(self, event):
        super().showEvent(event)
        if self.stroke and self.style().styleHint(QStyle.StyleHint.SH_Widget_Animate):
            self.stroke.start()

    def paintEvent(self, event):
        del event
        layout, height = self._lines(self.width())
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        offset = max(0, (self.height() - height) / 2)
        painter.setPen(QColor("#2c261f"))
        layout.draw(painter, QPointF(0, offset))
        if self.title and layout.lineCount():
            last = layout.lineAt(layout.lineCount() - 1)
            extent = min(self.width() - 4, last.naturalTextWidth())
            y = offset + height - 4
            path = QPainterPath(QPointF(2, y))
            path.quadTo(2 + extent * .45, y - 2, 2 + extent, y - .6)
            progress = (self.stroke.currentValue() if self.stroke.state() ==
                        QVariantAnimation.State.Running else 1.0)
            painter.setClipRect(0, 0, ceil(4 + extent * progress), self.height())
            pen = QPen(QColor("#f6c850"), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawPath(path)
        painter.end()
