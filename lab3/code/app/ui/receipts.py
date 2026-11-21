"""选择凭证图片和在应用内预览历史凭证。"""

from base64 import b64decode, b64encode
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImageReader, QPixmap
from PySide6.QtWidgets import QCheckBox, QDialog, QFileDialog, QHBoxLayout, QVBoxLayout, QWidget

from app.models.receipts import MAX_RECEIPT_BYTES, MAX_RECEIPTS
from app.ui.paper import Illustration
from app.ui.theme import button, label, panel


class ReceiptPicker(QWidget):
    """准备新文件，旧凭证的保留与否只作用于下一版。"""

    def __init__(self, copies: tuple[dict, ...]) -> None:
        super().__init__()
        self.setObjectName("card")
        self.paths = ()
        self.copies = copies
        self.keep = QCheckBox("在新版本中继续使用当前凭证")
        self.keep.setChecked(True)
        self.selection = label("尚未选择新凭证", "muted")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 17, 20, 17)
        layout.addWidget(Illustration("receipt-heading", 29, "上传凭证"))
        if copies:
            layout.addWidget(label("当前凭证：" + "、".join(item["filename"] for item in copies)))
            layout.addWidget(self.keep)
        actions = QHBoxLayout()
        actions.addWidget(button("选择凭证图片", self._choose))
        actions.addWidget(button("清除新选择", lambda: self.select_files(())))
        actions.addStretch(1)
        layout.addLayout(actions)
        layout.addWidget(self.selection)
        self.previews = QHBoxLayout()
        layout.addLayout(self.previews)
        layout.addWidget(label("JPG 或 PNG · 最多 3 张 · 每张不超过 4 MiB", "muted"))

    def _choose(self) -> None:
        filenames, _ = QFileDialog.getOpenFileNames(
            self, "选择收据或付款截图", "", "图片 (*.jpg *.jpeg *.png *.JPG *.JPEG *.PNG)",
        )
        if filenames:
            self.select_files(tuple(Path(name) for name in filenames))

    def select_files(self, paths: tuple[Path, ...]) -> None:
        """显示用户刚选择的本地文件名。"""
        self.paths = paths
        names = "、".join(path.name for path in paths)
        self.selection.setText(names or "尚未选择新凭证")
        while self.previews.count():
            self.previews.takeAt(0).widget().deleteLater()
        for path in paths[:MAX_RECEIPTS]:
            reader = QImageReader(str(path))
            dimensions = reader.size()
            if not dimensions.isValid() or dimensions.width() * dimensions.height() > 16_000_000:
                continue
            dimensions.scale(112, 76, Qt.AspectRatioMode.KeepAspectRatio)
            reader.setScaledSize(dimensions)
            image = reader.read()
            if not image.isNull():
                thumbnail = label()
                thumbnail.setPixmap(QPixmap.fromImage(image))
                thumbnail.setAccessibleName(path.name)
                self.previews.addWidget(thumbnail)

    @property
    def has_receipts(self) -> bool:
        """本次选择包括要保留的旧图和新增图片。"""
        return bool(self.paths or (self.keep.isChecked() and self.copies))

    def payload(self) -> dict:
        """受限读取选中文件，不传出本地路径。"""
        kept = len(self.copies) if self.keep.isChecked() else 0
        if kept + len(self.paths) > MAX_RECEIPTS:
            raise ValueError("保留和新上传的凭证合计最多 3 张。")
        attachments = []
        for path in self.paths:
            try:
                with path.open("rb") as source:
                    data = source.read(MAX_RECEIPT_BYTES + 1)
            except OSError as exc:
                raise ValueError(f"无法读取凭证：{path.name}") from exc
            if not data or len(data) > MAX_RECEIPT_BYTES:
                raise ValueError(f"凭证不能为空且每张最多 4 MiB：{path.name}")
            attachments.append({
                "filename": path.name, "data_base64": b64encode(data).decode("ascii"),
            })
        return {"attachments": attachments, "keep_receipts": self.keep.isChecked()}


def receipts_card(title: str, receipts: list[dict], preview) -> QWidget:
    """显示版本或申请凭证的文件名与预览入口。"""
    card, body = panel(margin=14)
    body.addWidget(label(title, "cardTitle"))
    for receipt in receipts:
        row = QHBoxLayout()
        row.addWidget(label(receipt["filename"]), 1)
        row.addWidget(button("查看凭证", lambda item=receipt: preview(item)))
        body.addLayout(row)
    return card


def preview_image(parent: QWidget, data: dict) -> None:
    """在当前窗口内加载图片，无需打开外部程序或写入临时文件。"""
    pixmap = QPixmap()
    if not pixmap.loadFromData(b64decode(data["data_base64"], validate=True)):
        raise ValueError("无法显示图片")
    extent = parent.screen().availableGeometry()
    preview = label()
    preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
    preview.setPixmap(pixmap.scaled(
        min(parent.width() - 48, extent.width() - 100),
        min(parent.height() - 130, extent.height() - 150),
        Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation,
    ))
    dialog = QDialog(parent)
    dialog.setWindowTitle("凭证预览")
    layout = QVBoxLayout(dialog)
    layout.addWidget(label(data["filename"], "cardTitle"))
    layout.addWidget(preview)
    layout.addWidget(button("关闭预览", dialog.accept))
    dialog.exec()
    dialog.deleteLater()
