"""An animated loop of the latest radar images around the configured location.

Each frame is the RZC rain rate over a square centred on the location,
drawn over a lightened swisstopo map, with its time and a colour legend.
The result is an animated GIF, a format every browser and the companion
apps show without help.
"""

from __future__ import annotations

import datetime
import io
import math
import zoneinfo

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from custom_components.meteoswiss.radar import wgs84_to_lv95

FRAMES = 5
RADIUS_KM = 30
# Pixels per km of radar grid; the radar pixels are 1 km.
SCALE = 8
SIZE = 2 * RADIUS_KM * SCALE
LEGEND_HEIGHT = 34
FOOTER_HEIGHT = 16
FRAME_MS = 700
LAST_FRAME_MS = 2000

BASEMAP_URL = (
    "https://wms.geo.admin.ch/?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap"
    "&LAYERS=ch.swisstopo.pixelkarte-grau&STYLES=&CRS=EPSG:2056"
    "&BBOX={0:.0f},{1:.0f},{2:.0f},{3:.0f}&WIDTH={4}&HEIGHT={4}&FORMAT=image/png"
)

# (lower bound in mm/h, colour, opacity).  Light rain lets the map show
# through; heavy rain is nearly opaque.
RAMP = (
    (0.1, (168, 200, 255), 120),
    (0.5, (110, 155, 255), 140),
    (1.0, (47, 108, 246), 160),
    (2.0, (22, 179, 164), 180),
    (5.0, (122, 209, 59), 195),
    (10.0, (245, 211, 28), 210),
    (20.0, (255, 140, 26), 220),
    (40.0, (232, 38, 42), 230),
    (70.0, (176, 25, 158), 235),
)


def crop_around(
    content: bytes, lat: float, lon: float
) -> tuple[np.ndarray, tuple[float, float, float, float]]:
    """Return the rain rate over the square around lat/lon, and its LV95 box.

    Parts of the square outside the radar grid read as no data.
    """
    import pyfive

    f = pyfive.File(io.BytesIO(content))
    where = f["where"].attrs
    x_scale = float(where["xscale"])
    y_scale = float(where["yscale"])
    left, top = wgs84_to_lv95(float(where["UL_lat"]), float(where["UL_lon"]))
    left = round(left / x_scale) * x_scale
    top = round(top / y_scale) * y_scale
    east, north = wgs84_to_lv95(lat, lon)
    col = math.floor((east - left) / x_scale)
    row = math.floor((top - north) / y_scale)

    data = f["dataset1/data1/data"]
    rows, cols = int(where["ysize"]), int(where["xsize"])
    out = np.full((2 * RADIUS_KM, 2 * RADIUS_KM), np.nan)
    r0, c0 = row - RADIUS_KM, col - RADIUS_KM
    rs, re = max(r0, 0), min(r0 + 2 * RADIUS_KM, rows)
    cs, ce = max(c0, 0), min(c0 + 2 * RADIUS_KM, cols)
    if rs < re and cs < ce:
        out[rs - r0 : re - r0, cs - c0 : ce - c0] = np.asarray(data[rs:re, cs:ce])
    box = (
        left + c0 * x_scale,
        top - (r0 + 2 * RADIUS_KM) * y_scale,
        left + (c0 + 2 * RADIUS_KM) * x_scale,
        top - r0 * y_scale,
    )
    return out, box


def basemap_url(box: tuple[float, float, float, float]) -> str:
    """Return the swisstopo WMS URL of the map under the square."""
    return BASEMAP_URL.format(*box, SIZE)


def prepare_basemap(content: bytes | None) -> Image.Image:
    """Lighten and flatten the map, or return a blank one without it.

    Flattening is not only cosmetic: without the hillshade's texture the
    GIF is a third of the size.
    """
    if not content:
        return Image.new("RGB", (SIZE, SIZE), (235, 235, 235))
    g = Image.open(io.BytesIO(content)).convert("L").resize((SIZE, SIZE))
    g = Image.blend(g, Image.new("L", g.size, 255), 0.45)
    step = 256 // 12
    g = g.point(lambda v: min(255, (v // step) * step + step // 2))
    return g.convert("RGB")


def _colourise(rate: np.ndarray) -> Image.Image:
    rgba = np.zeros(rate.shape + (4,), dtype=np.uint8)
    values = np.nan_to_num(rate, nan=0.0)
    for low, rgb, alpha in RAMP:
        mask = values >= low
        rgba[mask, :3] = rgb
        rgba[mask, 3] = alpha
    layer = Image.fromarray(rgba, "RGBA")
    return layer.resize((SIZE, SIZE), Image.Resampling.NEAREST)


def _legend(font: ImageFont.ImageFont) -> Image.Image:
    strip = Image.new("RGB", (SIZE, LEGEND_HEIGHT), (255, 255, 255))
    d = ImageDraw.Draw(strip)
    d.text((6, 10), "mm/h", font=font, fill=(0, 0, 0))
    width = (SIZE - 70) / len(RAMP)
    for k, (low, rgb, _) in enumerate(RAMP):
        x = 50 + k * width
        d.rectangle((x, 4, x + width - 2, 16), fill=rgb)
        d.text((x, 18), f"{low:g}", font=font, fill=(60, 60, 60))
    return strip


def render_loop(
    frames: list[tuple[datetime.datetime, np.ndarray]],
    base: Image.Image,
    time_zone: str,
) -> bytes:
    """Return an animated GIF of the frames, oldest first."""
    tz = zoneinfo.ZoneInfo(time_zone)
    font = ImageFont.load_default(size=18)
    small = ImageFont.load_default(size=12)
    legend = _legend(small)
    newest = frames[-1][0]
    centre = SIZE // 2
    images = []
    for i, (slot, rate) in enumerate(frames):
        im = base.copy()
        layer = _colourise(rate)
        im.paste(layer, (0, 0), layer)
        d = ImageDraw.Draw(im)
        d.ellipse((centre - 7, centre - 7, centre + 7, centre + 7), outline=0, width=3)
        d.ellipse((centre - 3, centre - 3, centre + 3, centre + 3), fill=0)

        minutes = int((newest - slot).total_seconds() // 60)
        label = slot.astimezone(tz).strftime("%H:%M") + (
            "  latest" if minutes == 0 else f"  -{minutes} min"
        )
        d.rounded_rectangle(
            (8, 8, 24 + d.textlength(label, font=font), 40), 6, fill=(255, 255, 255)
        )
        d.text((16, 13), label, font=font, fill=(0, 0, 0))
        for k in range(len(frames)):
            x = SIZE - 16 - (len(frames) - 1 - k) * 14
            d.ellipse(
                (x - 4, 20, x + 4, 28),
                fill=(0, 0, 0) if k == i else (255, 255, 255),
                outline=(0, 0, 0),
            )

        full = Image.new(
            "RGB", (SIZE, SIZE + LEGEND_HEIGHT + FOOTER_HEIGHT), (255, 255, 255)
        )
        full.paste(im, (0, 0))
        full.paste(legend, (0, SIZE))
        ImageDraw.Draw(full).text(
            (SIZE - 6, SIZE + LEGEND_HEIGHT + 2),
            "Radar © MeteoSwiss · map © swisstopo",
            font=small,
            fill=(110, 110, 110),
            anchor="ra",
        )
        images.append(
            full.quantize(
                colors=96,
                method=Image.Quantize.MEDIANCUT,
                dither=Image.Dither.NONE,
            )
        )

    buf = io.BytesIO()
    images[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=images[1:],
        duration=[FRAME_MS] * (len(images) - 1) + [LAST_FRAME_MS],
        loop=0,
        disposal=1,
    )
    return buf.getvalue()
