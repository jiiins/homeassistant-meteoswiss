import logging
import pprint
import typing
from datetime import date, datetime
from decimal import Decimal

from hamsclientfork.client import StationType
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfVolumetricFlux
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.meteoswiss import MeteoSwissDataUpdateCoordinator
from custom_components.meteoswiss.const import (
    CONF_FORECAST_NAME,
    CONF_POSTCODE,
    CONF_PRECIPITATION_STATION,
    CONF_REAL_TIME_NAME,
    CONF_REAL_TIME_PRECIPITATION_NAME,
    CONF_STATION,
    DOMAIN,
    SENSOR_DATA_ID,
    SENSOR_TYPE_CLASS,
    SENSOR_TYPE_ICON,
    SENSOR_TYPE_NAME,
    SENSOR_TYPE_UNIT,
    SENSOR_TYPES,
)
from custom_components.meteoswiss.radar import MeteoSwissRadarCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up all sensors."""
    _LOGGER.debug("Starting async setup platform for sensor")
    c: MeteoSwissDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        [MeteoSwissRadarSensor(entry.entry_id, c.data[CONF_FORECAST_NAME], c.radar)]
    )

    if c.weather_station:
        async_add_entities(
            [
                MeteoSwissSensor(entry.entry_id, typ, c, StationType.WEATHER)
                for typ in SENSOR_TYPES
            ],
            True,
        )
    else:
        _LOGGER.debug(
            "The data update coordinator has no real-time weather station configured"
            + " — not providing weather sensor data."
        )
    if c.precipitation_station:
        async_add_entities(
            [
                MeteoSwissSensor(entry.entry_id, typ, c, StationType.PRECIPITATION)
                for typ in SENSOR_TYPES
            ],
            True,
        )
    else:
        _LOGGER.debug(
            "The data update coordinator has no real-time precipitation station configured"
            + " — not providing precipitation sensor data."
        )


class MeteoSwissSensor(
    CoordinatorEntity[MeteoSwissDataUpdateCoordinator],
    SensorEntity,
):
    """Represents a sensor from MeteoSwiss."""

    def __init__(
        self,
        integration_id: str,
        sensor_type: typing.Any,
        coordinator: MeteoSwissDataUpdateCoordinator,
        station_type: StationType,
    ):
        super().__init__(coordinator)
        self._station_type = station_type
        self._attr_unique_id = "sensor.%s-%s%s" % (
            integration_id,
            sensor_type,
            "-precipitation" if station_type == StationType.PRECIPITATION else "",
        )
        self._state = None
        self._type = sensor_type
        self._attr_native_unit_of_measurement = SENSOR_TYPES[self._type][
            SENSOR_TYPE_UNIT
        ]
        self._attr_icon = SENSOR_TYPES[self._type][SENSOR_TYPE_ICON]
        self._attr_device_class = SENSOR_TYPES[self._type][SENSOR_TYPE_CLASS]
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._data = coordinator.data
        self._attr_station = coordinator.data[
            CONF_STATION
            if station_type == StationType.WEATHER
            else CONF_PRECIPITATION_STATION
        ]
        self._attr_postcode = coordinator.data[CONF_POSTCODE]

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        x = SENSOR_TYPES[self._type][SENSOR_TYPE_NAME]
        name_key = (
            CONF_REAL_TIME_NAME
            if self._station_type == StationType.WEATHER
            else CONF_REAL_TIME_PRECIPITATION_NAME
        )
        return f"{self._data[name_key]} {x}"

    @property
    def native_value(self) -> StateType | date | datetime | Decimal:
        dataId = SENSOR_TYPES[self._type][SENSOR_DATA_ID]
        data: StateType | date | datetime | Decimal = None
        if (
            self._attr_station not in self._data["condition_by_station"]
            or not self._data["condition_by_station"][self._attr_station]
        ):
            pass
        else:
            try:
                data = self._data["condition_by_station"][self._attr_station][dataId]
            except Exception:
                _LOGGER.warning(
                    "Real-time weather station returned bad data:\n%s",
                    pprint.pformat(self._data),
                )
                data = None
        return data

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        available = False
        dataId = SENSOR_TYPES[self._type][SENSOR_DATA_ID]
        if (
            self._attr_station not in self._data["condition_by_station"]
            or not self._data["condition_by_station"][self._attr_station]
        ):
            pass
        else:
            try:
                available = (
                    self._data["condition_by_station"][self._attr_station][dataId]
                    is not None
                )
            except Exception:
                available = False
        return available

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle data update."""
        self._data = self.coordinator.data
        self.async_write_ha_state()


class MeteoSwissRadarSensor(
    CoordinatorEntity[MeteoSwissRadarCoordinator],
    SensorEntity,
):
    """Rain rate from the MeteoSwiss weather radar at the configured location."""

    _attr_attribution = "Source: MeteoSwiss"
    _attr_device_class = SensorDeviceClass.PRECIPITATION_INTENSITY
    _attr_icon = "mdi:radar"
    _attr_native_unit_of_measurement = UnitOfVolumetricFlux.MILLIMETERS_PER_HOUR
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        integration_id: str,
        forecast_name: str,
        coordinator: MeteoSwissRadarCoordinator,
    ):
        super().__init__(coordinator)
        self._attr_unique_id = f"sensor.{integration_id}-radar-precipitation"
        self._attr_name = f"{forecast_name} radar precipitation"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.rate

    @property
    def extra_state_attributes(self) -> dict[str, typing.Any] | None:
        if self.coordinator.data is None:
            return None
        # End of the 5-minute interval the reading covers.
        return {"observation_time": self.coordinator.data.time.isoformat()}
