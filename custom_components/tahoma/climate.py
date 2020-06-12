"""Support for Tahoma climate."""
from datetime import timedelta
import logging
from typing import List, Optional

from homeassistant.core import callback, State
from homeassistant.helpers.event import async_track_state_change
from homeassistant.const import (
    ATTR_TEMPERATURE,
    DEVICE_CLASS_TEMPERATURE,
    EVENT_HOMEASSISTANT_START,
    STATE_OFF,
    STATE_ON,
    STATE_UNKNOWN,
    TEMP_CELSIUS,
)
from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    CURRENT_HVAC_HEAT,
    CURRENT_HVAC_IDLE,
    CURRENT_HVAC_OFF,
    HVAC_MODE_HEAT,
    HVAC_MODE_AUTO,
    PRESET_AWAY,
    PRESET_HOME,
    PRESET_NONE,
    PRESET_SLEEP,
    SUPPORT_PRESET_MODE,
    SUPPORT_TARGET_TEMPERATURE,
    ATTR_PRESET_MODE,
    HVAC_MODE_OFF,
)

from .const import (
    DOMAIN,
    TAHOMA_TYPES,
)
from .tahoma_device import TahomaDevice

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=120)

SUPPORTED_CLIMATE_DEVICES = [
    "SomfyThermostat",
    # "AtlanticElectricalHeater"
]

COMMAND_REFRESH = "refreshState"
COMMAND_EXIT_DEROGATION = "exitDerogation"
COMMAND_SET_DEROGATION = "setDerogation"

ST_DEROGATION_TYPE_STATE = 'somfythermostat:DerogationTypeState'
ST_HEATING_MODE_STATE = 'somfythermostat:HeatingModeState'
ST_DEROGATION_HEATING_MODE_STATE = 'somfythermostat:DerogationHeatingModeState'
CORE_TARGET_TEMPERATURE_STATE = 'core:TargetTemperatureState'
CORE_DEROGATED_TARGET_TEMPERATURE_STATE = 'core:DerogatedTargetTemperatureState'

PRESET_FREEZE = "freeze"

STATE_DEROGATION_FURTHER_NOTICE = "further_notice"
STATE_DEROGATION_NEXT_MODE = "next_mode"
STATE_DEROGATION_DATE = "date"
STATE_PRESET_AT_HOME = "atHomeMode"
STATE_PRESET_AWAY = "awayMode"
STATE_PRESET_FREEZE = "freezeMode"
STATE_PRESET_MANUAL = "manualMode"
STATE_PRESET_SLEEPING_MODE = "sleepingMode"

MAP_HVAC_MODE = {
    STATE_DEROGATION_DATE: HVAC_MODE_AUTO,
    STATE_DEROGATION_NEXT_MODE: HVAC_MODE_HEAT,
    STATE_DEROGATION_FURTHER_NOTICE: HVAC_MODE_HEAT,
    STATE_ON: HVAC_MODE_HEAT,
    STATE_OFF: HVAC_MODE_OFF,
}
MAP_PRESET = {
    STATE_PRESET_AT_HOME: PRESET_HOME,
    STATE_PRESET_AWAY: PRESET_AWAY,
    STATE_PRESET_FREEZE: PRESET_FREEZE,
    STATE_PRESET_MANUAL: PRESET_NONE,
    STATE_PRESET_SLEEPING_MODE: PRESET_SLEEP,
}
MAP_PRESET_REVERSE = {
    PRESET_HOME: STATE_PRESET_AT_HOME,
    PRESET_AWAY: STATE_PRESET_AWAY,
    PRESET_FREEZE: STATE_PRESET_FREEZE,
    PRESET_NONE: STATE_PRESET_MANUAL,
    PRESET_SLEEP: STATE_PRESET_SLEEPING_MODE,
}
TAHOMA_TYPE_HEATING_SYSTEM = "HeatingSystem"


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up the Tahoma sensors from a config entry."""

    data = hass.data[DOMAIN][entry.entry_id]

    entry.add_update_listener(update_listener)

    entities = []
    controller = data.get("controller")

    for device in data.get("devices"):
        if TAHOMA_TYPES[device.uiclass] == "climate" and device.widget in SUPPORTED_CLIMATE_DEVICES:
            options = dict(entry.options)
            if device.url in options[TAHOMA_TYPE_HEATING_SYSTEM]:
                sensor_id = options[DEVICE_CLASS_TEMPERATURE][device.url]
                entities.append(TahomaClimate(device, controller, sensor_id))
            else:
                entities.append(TahomaClimate(device, controller))

    async_add_entities(entities)


async def update_listener(hass, entry):
    """Handle options update."""
    options = dict(entry.options)
    for entity in hass.data["climate"].entities:
        if entity.unique_id in options[TAHOMA_TYPE_HEATING_SYSTEM]:
            entity.set_temperature_sensor(options[DEVICE_CLASS_TEMPERATURE][entity.unique_id])
            entity.schedule_update_ha_state()


class TahomaClimate(TahomaDevice, ClimateEntity):
    """Representation of a Tahoma thermostat."""

    def __init__(self, tahoma_device, controller, sensor_id=None):
        """Initialize the sensor."""
        super().__init__(tahoma_device, controller)
        if COMMAND_REFRESH in self.tahoma_device.command_definitions:
            self.apply_action(COMMAND_REFRESH)
        self.controller.get_states([self.tahoma_device])
        self._uiclass = tahoma_device.uiclass
        self._unique_id = tahoma_device.url
        self._widget = tahoma_device.widget
        self._temp_sensor_entity_id = sensor_id
        self._current_temperature = 0
        if self._widget == "SomfyThermostat":
            self._hvac_modes = [HVAC_MODE_HEAT, HVAC_MODE_AUTO]
            self._hvac_mode = MAP_HVAC_MODE[
                self.tahoma_device.active_states['somfythermostat:DerogationTypeState']
            ]
            self._current_hvac_modes = CURRENT_HVAC_IDLE
            self._preset_mode = MAP_PRESET[
                self.tahoma_device.active_states['somfythermostat:HeatingModeState']
                if self._hvac_mode == HVAC_MODE_AUTO
                else self.tahoma_device.active_states['somfythermostat:DerogationHeatingModeState']
            ]
            self._preset_modes = [
                PRESET_NONE, PRESET_FREEZE, PRESET_SLEEP, PRESET_AWAY, PRESET_HOME
            ]
            self._target_temp = (
                self.tahoma_device.active_states['core:TargetTemperatureState']
                if self._hvac_mode == HVAC_MODE_AUTO
                else self.tahoma_device.active_states['core:DerogatedTargetTemperatureState']
            )
            self._stored_target_temp = self._target_temp
        self._is_away = None

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        if self._temp_sensor_entity_id is not None:
            async_track_state_change(
                self.hass, self._temp_sensor_entity_id, self._async_temp_sensor_changed
            )

        @callback
        def _async_startup(event):
            """Init on startup."""
            if self._temp_sensor_entity_id is not None:
                temp_sensor_state = self.hass.states.get(self._temp_sensor_entity_id)
                if temp_sensor_state and temp_sensor_state.state != STATE_UNKNOWN:
                    self.update_temp(temp_sensor_state)

        self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_START, _async_startup)

        self.schedule_update_ha_state(True)

    async def _async_temp_sensor_changed(self, entity_id: str, old_state: State,
                                         new_state: State) -> None:
        """Handle temperature changes."""
        if new_state is None:
            return
        if old_state == new_state:
            return

        self.update_temp(new_state)
        self.schedule_update_ha_state()

    @callback
    def update_temp(self, state=None):
        """Update thermostat with latest state from sensor."""
        if state is None:
            state = self.hass.states.get(self._temp_sensor_entity_id)

        try:
            self._current_temperature = (
                0 if state.state == STATE_UNKNOWN
                else float(state.state)
            )
        except ValueError as ex:
            _LOGGER.error("Unable to update from sensor: %s", ex)

    def update(self):
        """Update the state."""
        if COMMAND_REFRESH in self.tahoma_device.command_definitions:
            self.apply_action(COMMAND_REFRESH)
        self.controller.get_states([self.tahoma_device])
        self.update_temp(None)
        if self._widget == "SomfyThermostat":
            self._hvac_mode = MAP_HVAC_MODE[
                self.tahoma_device.active_states['somfythermostat:DerogationTypeState']
            ]
            self._target_temp = (
                self.tahoma_device.active_states['core:TargetTemperatureState']
                if self._hvac_mode == HVAC_MODE_AUTO
                else self.tahoma_device.active_states['core:DerogatedTargetTemperatureState']
            )
            self._preset_mode = MAP_PRESET[
                self.tahoma_device.active_states['somfythermostat:HeatingModeState']
                if self._hvac_mode == HVAC_MODE_AUTO
                else self.tahoma_device.active_states['somfythermostat:DerogationHeatingModeState']
            ]
            self._current_hvac_modes = (
                CURRENT_HVAC_IDLE
                if self._current_temperature is None or self._current_temperature > self._target_temp
                else CURRENT_HVAC_HEAT
            )

    @property
    def available(self) -> bool:
        """If the device hasn't been able to connect, mark as unavailable."""
        return bool(self._current_temperature != 0)

    @property
    def hvac_mode(self) -> str:
        """Return hvac operation ie. heat, cool mode."""
        return self._hvac_mode

    @property
    def hvac_modes(self) -> List[str]:
        """Return the list of available hvac operation modes."""
        return self._hvac_modes

    @property
    def hvac_action(self) -> Optional[str]:
        """Return the current running hvac operation if supported."""
        return self._current_hvac_modes

    def set_hvac_mode(self, hvac_mode: str) -> None:
        """Set new target hvac mode."""
        if self._widget == "SomfyThermostat":
            if hvac_mode == HVAC_MODE_AUTO and self._hvac_mode != HVAC_MODE_AUTO:
                self._stored_target_temp = self._target_temp
                self.apply_action(COMMAND_EXIT_DEROGATION)
            elif hvac_mode == HVAC_MODE_HEAT and self._hvac_mode != HVAC_MODE_HEAT:
                self._target_temp = self._stored_target_temp
                self._preset_mode = PRESET_NONE
                self.apply_action(COMMAND_SET_DEROGATION, self._target_temp,
                                  STATE_DEROGATION_FURTHER_NOTICE)

    @property
    def supported_features(self) -> int:
        """Return the list of supported features."""
        return SUPPORT_PRESET_MODE | SUPPORT_TARGET_TEMPERATURE

    @property
    def temperature_sensor(self) -> str:
        """Return the id of the temperature sensor"""
        return self._temp_sensor_entity_id

    def set_temperature_sensor(self, sensor_id: str):
        self._temp_sensor_entity_id = sensor_id
        self.schedule_update_ha_state()

    @property
    def temperature_unit(self) -> str:
        """Return the unit of measurement used by the platform."""
        return TEMP_CELSIUS

    @property
    def current_temperature(self) -> Optional[float]:
        """Return the current temperature"""
        return self._current_temperature

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        return self._target_temp

    def set_temperature(self, **kwargs) -> None:
        """Set new target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        if self._widget == "SomfyThermostat":
            if temperature < 15:
                self.apply_action("setDerogation", "freezeMode", "further_notice")
            if temperature > 26:
                temperature = 26
            self._target_temp = temperature
            self.apply_action("setDerogation", temperature, "further_notice")
            self.apply_action("setModeTemperature", "manualMode", temperature)

    @property
    def preset_mode(self) -> Optional[str]:
        """Return the current preset mode, e.g., home, away, temp.

        Requires SUPPORT_PRESET_MODE.
        """
        return self._preset_mode

    @property
    def preset_modes(self) -> Optional[List[str]]:
        """Return a list of available preset modes.

        Requires SUPPORT_PRESET_MODE.
        """
        return self._preset_modes

    def set_preset_mode(self, preset_mode: str) -> None:
        """Set new preset mode."""
        if preset_mode not in self.preset_modes:
            _LOGGER.error(
                "Preset " + preset_mode + " is not available for " + self._name
            )
            return
        if self._widget == "SomfyThermostat":
            if preset_mode in [PRESET_FREEZE, PRESET_SLEEP, PRESET_AWAY, PRESET_HOME]:
                if self._preset_mode == preset_mode:
                    return
                self._preset_mode = preset_mode
                self._stored_target_temp = self._target_temp
                self.apply_action("setDerogation", MAP_PRESET_REVERSE[preset_mode],
                                  "further_notice")
            elif preset_mode == PRESET_NONE and not self._preset_mode == PRESET_NONE:
                self._preset_mode = PRESET_NONE
                self._target_temp = self._stored_target_temp
                self.apply_action("setDerogation", self._target_temp, "further_notice")
                self.apply_action("setModeTemperature", "manualMode", self._target_temp)

