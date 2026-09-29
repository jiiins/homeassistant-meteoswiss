"""Precipitation rate at a point from the MeteoSwiss weather radar.

MeteoSwiss publishes the radar composite RZC as open data: a 1 km grid of
rain rate in mm/h over Switzerland and its surroundings, one ODIM HDF5 file
per 5-minute interval, usually online well under two minutes after the
interval ends.  That is much faster than any rain gauge feed, which makes it
the quickest "is it raining here right now" signal available.

See https://opendatadocs.meteoswiss.ch/d-radar-data/d1-precipitation-radar-products
"""

from __future__ import annotations

import asyncio
import datetime
import io
import logging
import math
from dataclasses import dataclass

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from custom_components.meteoswiss.const import DOMAIN, USER_AGENT

_LOGGER = logging.getLogger(__name__)

RADAR_URL = (
    "https://data.geo.admin.ch/ch.meteoschweiz.ogd-radar-precip"
    "/{t:%Y%m%d}-ch/rzc{t:%y}{doy:03d}{t:%H%M}vl.001.h5"
)
SLOT = datetime.timedelta(minutes=5)
# How often to look for a new image.  Only a new image is downloaded
# (about 30 kB); a look for one that is not out yet is a single 403.
POLL_INTERVAL = datetime.timedelta(minutes=1)
# Images appear roughly 25 to 70 seconds after their interval ends.
PUBLISH_DELAY = datetime.timedelta(seconds=30)
# Past this age the reading is no longer "now", so the sensor goes
# unavailable instead of repeating it.
MAX_AGE = datetime.timedelta(minutes=20)


def wgs84_to_lv95(lat: float, lon: float) -> tuple[float, float]:
    """Convert WGS84 degrees to Swiss LV95 (east, north) metres.

    swisstopo's approximate formulas.  Within 10 m of the exact projection
    across the whole radar grid, against 1000 m pixels.
    """
    phi = (lat * 3600 - 169028.66) / 10000
    lam = (lon * 3600 - 26782.5) / 10000
    east = (
        2600072.37
        + 211455.93 * lam
        - 10938.51 * lam * phi
        - 0.36 * lam * phi**2
        - 44.54 * lam**3
    )
    north = (
        1200147.07
        + 308807.95 * phi
        + 3745.25 * lam**2
        + 76.63 * phi**2
        - 194.56 * lam**2 * phi
        + 119.79 * phi**3
    )
    return east, north


def latest_slot(now: datetime.datetime) -> datetime.datetime:
    """Return the end of the newest interval whose image may be out by now."""
    t = now.astimezone(datetime.UTC) - PUBLISH_DELAY
    return t.replace(minute=t.minute - t.minute % 5, second=0, microsecond=0)


def radar_url(slot: datetime.datetime) -> str:
    """Return the URL of the RZC image for the interval ending at slot."""
    t = slot.astimezone(datetime.UTC)
    return RADAR_URL.format(t=t, doy=t.timetuple().tm_yday)


def read_rain_rate(content: bytes, lat: float, lon: float) -> float | None:
    """Return the rain rate in mm/h at lat/lon from an RZC image.

    None means the radar has no data for that pixel.  The grid is read
    from the file, so a change of grid does not silently shift the pixel.
    """
    import pyfive

    f = pyfive.File(io.BytesIO(content))
    where = f["where"].attrs
    x_scale = float(where["xscale"])
    y_scale = float(where["yscale"])
    # Snap the upper-left corner to the grid; the formula is off by metres.
    left, top = wgs84_to_lv95(float(where["UL_lat"]), float(where["UL_lon"]))
    left = round(left / x_scale) * x_scale
    top = round(top / y_scale) * y_scale

    east, north = wgs84_to_lv95(lat, lon)
    col = math.floor((east - left) / x_scale)
    row = math.floor((top - north) / y_scale)
    if not (0 <= col < int(where["xsize"]) and 0 <= row < int(where["ysize"])):
        raise ValueError(
            f"{lat}, {lon} is outside the MeteoSwiss radar composite",
        )

    rate = float(f["dataset1/data1/data"][row, col])
    if math.isnan(rate):
        return None
    return round(rate, 2)


@dataclass(frozen=True)
class RadarReading:
    """One radar reading at the configured location."""

    time: datetime.datetime  # end of the 5-minute interval, UTC
    rate: float | None  # mm/h; None where the radar has no data


class MeteoSwissRadarCoordinator(DataUpdateCoordinator[RadarReading]):
    """Fetch each new radar image once and read one pixel from it."""

    def __init__(self, hass: HomeAssistant, lat: float, lon: float) -> None:
        """Initialize."""
        self.lat = lat
        self.lon = lon
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} radar",
            update_interval=POLL_INTERVAL,
        )

    async def _fetch(self, slot: datetime.datetime) -> bytes | None:
        """Download one image, or None if it has not been published yet."""
        session = async_get_clientsession(self.hass)
        async with asyncio.timeout(15):
            async with session.get(
                radar_url(slot),
                headers={"User-Agent": USER_AGENT},
            ) as resp:
                # The file store answers 403, not 404, for a missing file.
                if resp.status in (403, 404):
                    return None
                resp.raise_for_status()
                return await resp.read()

    async def _async_update_data(self) -> RadarReading:
        """Return the newest reading, downloading only images not yet seen."""
        now = dt_util.utcnow()
        current = self.data
        newest = latest_slot(now)
        if current is not None and current.time >= newest:
            return current

        # A reading still inside MAX_AGE survives a late image or a failed
        # download; only a stale one makes the sensor unavailable.
        fresh = current is not None and now - current.time < MAX_AGE
        for slot in (newest, newest - SLOT):
            if current is not None and slot <= current.time:
                break
            url = radar_url(slot)
            try:
                content = await self._fetch(slot)
                if content is None:
                    continue
                rate = await self.hass.async_add_executor_job(
                    read_rain_rate,
                    content,
                    self.lat,
                    self.lon,
                )
            except (TimeoutError, aiohttp.ClientError) as exc:
                if fresh:
                    _LOGGER.debug("Cannot fetch %s: %s", url, exc)
                    return current
                raise UpdateFailed(f"Cannot fetch {url}: {exc}") from exc
            except Exception as exc:
                raise UpdateFailed(f"Cannot read {url}: {exc}") from exc
            return RadarReading(time=slot, rate=rate)

        if fresh:
            return current
        if current is not None:
            raise UpdateFailed(f"No new radar image since {current.time}")
        raise UpdateFailed(f"No radar image published for {newest - SLOT} or later")
