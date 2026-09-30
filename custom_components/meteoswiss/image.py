"""The radar loop as an image entity."""

import datetime
import logging

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.meteoswiss import MeteoSwissDataUpdateCoordinator
from custom_components.meteoswiss.const import CONF_FORECAST_NAME, DOMAIN
from custom_components.meteoswiss.radar import MeteoSwissRadarCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the radar loop."""
    c: MeteoSwissDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [MeteoSwissRadarLoop(hass, entry.entry_id, c.data[CONF_FORECAST_NAME], c.radar)]
    )


class MeteoSwissRadarLoop(
    CoordinatorEntity[MeteoSwissRadarCoordinator],
    ImageEntity,
):
    """An animated GIF of the latest radar images around the location.

    An image entity rather than a camera: the frontend reloads an image
    only when it changes, every 5 minutes here, where a camera card would
    fetch it again every 10 seconds.
    """

    _attr_attribution = "Radar: MeteoSwiss, map: swisstopo"
    _attr_content_type = "image/gif"
    _attr_icon = "mdi:radar"

    def __init__(
        self,
        hass: HomeAssistant,
        integration_id: str,
        forecast_name: str,
        coordinator: MeteoSwissRadarCoordinator,
    ) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        ImageEntity.__init__(self, hass)
        self._attr_unique_id = f"image.{integration_id}-radar-loop"
        self._attr_name = f"{forecast_name} radar loop"
        self._attr_image_last_updated = coordinator.loop_updated

    @property
    def available(self) -> bool:
        """Only once there is a loop to show."""
        return self.coordinator.loop_image is not None

    @property
    def image_last_updated(self) -> datetime.datetime | None:
        return self.coordinator.loop_updated

    async def async_image(self) -> bytes | None:
        return self.coordinator.loop_image

    @callback
    def _handle_coordinator_update(self) -> None:
        self._attr_image_last_updated = self.coordinator.loop_updated
        super()._handle_coordinator_update()
