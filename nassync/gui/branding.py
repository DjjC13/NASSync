"""The NASSync mark, and helpers for putting it on screen.

The logo is a source above a target with a single downward arrow between them --
deliberately one-way, because that is exactly what the tool does. It is kept as
inline SVG so there is no binary asset to lose, and so it stays crisp at every
size from a 16px window icon upward.
"""

from __future__ import annotations

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

LOGO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#58A6FF"/>
      <stop offset="1" stop-color="#1F6FEB"/>
    </linearGradient>
  </defs>
  <rect x="2" y="2" width="60" height="60" rx="15" fill="url(#bg)"/>
  <rect x="13" y="12" width="38" height="11" rx="5.5" fill="#FFFFFF" opacity="0.96"/>
  <circle cx="44" cy="17.5" r="2.1" fill="#1F6FEB"/>
  <rect x="13" y="41" width="38" height="11" rx="5.5" fill="#FFFFFF" opacity="0.72"/>
  <circle cx="44" cy="46.5" r="2.1" fill="#1F6FEB"/>
  <path d="M32 27.5 L32 34.5 M27.5 31 L32 36 L36.5 31"
        stroke="#FFFFFF" stroke-width="3.2" fill="none"
        stroke-linecap="round" stroke-linejoin="round"/>
</svg>"""


def _renderer() -> QSvgRenderer:
    return QSvgRenderer(QByteArray(LOGO_SVG.encode("utf-8")))


def logo_pixmap(size: int, device_pixel_ratio: float = 1.0) -> QPixmap:
    """Render the mark at *size* logical pixels, sharp on high-DPI displays."""
    physical = max(1, int(round(size * device_pixel_ratio)))
    image = QImage(physical, physical, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)

    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    _renderer().render(painter, QRectF(0, 0, physical, physical))
    painter.end()

    pixmap = QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(device_pixel_ratio)
    return pixmap


def logo_icon() -> QIcon:
    """A multi-resolution window/taskbar icon."""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(logo_pixmap(size))
    return icon


def write_svg(path) -> None:
    """Write the mark out as a standalone .svg (used for the README)."""
    from pathlib import Path

    Path(path).write_text(LOGO_SVG, encoding="utf-8")


#: Sizes Windows picks between for the taskbar, Explorer, and Alt-Tab.
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def write_ico(path, sizes=ICO_SIZES) -> None:
    """Write the mark as a multi-resolution .ico for the packaged executable.

    Qt's own ICO writer stores a single image, which leaves Windows rescaling
    one bitmap for every context. This assembles a proper multi-size icon with
    PNG-compressed entries instead, so Explorer and the taskbar each get a
    rendering drawn at their own size.
    """
    from pathlib import Path

    images: list[tuple[int, bytes]] = []
    for size in sizes:
        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        logo_pixmap(size).toImage().save(buffer, "PNG")
        images.append((size, bytes(buffer.data())))

    header = b"\x00\x00\x01\x00" + len(images).to_bytes(2, "little")
    offset = len(header) + 16 * len(images)

    directory = b""
    for size, data in images:
        directory += bytes(
            [
                0 if size >= 256 else size,  # 0 encodes 256 in the ICO header
                0 if size >= 256 else size,
                0,  # palette size: 0 for true colour
                0,  # reserved
            ]
        )
        directory += (1).to_bytes(2, "little")   # colour planes
        directory += (32).to_bytes(2, "little")  # bits per pixel
        directory += len(data).to_bytes(4, "little")
        directory += offset.to_bytes(4, "little")
        offset += len(data)

    Path(path).write_bytes(header + directory + b"".join(img for _, img in images))
