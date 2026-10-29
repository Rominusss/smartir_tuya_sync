"""SmartIR climate: IR commands (SmartIR) + state feedback from a Tuya climate."""
from __future__ import annotations

import json
import logging
import os.path
from time import monotonic

import voluptuous as vol
from homeassistant.components.climate import PLATFORM_SCHEMA as CLIMATE_PLATFORM_SCHEMA
from homeassistant.components.climate.const import HVACMode
from homeassistant.const import (
    ATTR_TEMPERATURE,
    CONF_NAME,
    PRECISION_WHOLE,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import callback
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

# SmartIR must be installed: its code files, downloader and controllers are reused as-is.
from custom_components.smartir import COMPONENT_ABS_DIR, Helper
from custom_components.smartir.climate import (
    CONF_CONTROLLER_DATA,
    CONF_DELAY,
    CONF_DEVICE_CODE,
    CONF_HUMIDITY_SENSOR,
    CONF_TEMPERATURE_SENSOR,
    CONF_UNIQUE_ID,
    DEFAULT_DELAY,
    SmartIRClimate,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = "SmartIR Tuya Sync Climate"
CONF_TUYA_ENTITY = "tuya_entity"
CONF_SYNC_DELAY = "sync_delay"
DEFAULT_SYNC_DELAY = 8.0

PLATFORM_SCHEMA = CLIMATE_PLATFORM_SCHEMA.extend(
    {
        vol.Optional(CONF_UNIQUE_ID): cv.string,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Required(CONF_DEVICE_CODE): cv.positive_int,
        vol.Required(CONF_CONTROLLER_DATA): cv.string,
        vol.Optional(CONF_DELAY, default=DEFAULT_DELAY): cv.positive_float,
        vol.Required(CONF_TUYA_ENTITY): cv.entity_id,
        vol.Optional(CONF_TEMPERATURE_SENSOR): cv.entity_id,
        vol.Optional(CONF_HUMIDITY_SENSOR): cv.entity_id,
        vol.Optional(CONF_SYNC_DELAY, default=DEFAULT_SYNC_DELAY): cv.positive_float,
    }
)

# Tuya fan names -> possible SmartIR names (first one present in the IR file wins).
FAN_ALIASES = {
    "medium": ("mid", "middle"),
    "mid": ("medium", "middle"),
    "middle": ("medium", "mid"),
    "strong": ("high", "turbo"),
    "turbo": ("high", "strong"),
    "mute": ("low", "quiet"),
    "quiet": ("low", "mute"),
}


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the platform (same device-file loading as SmartIR)."""
    device_code = config[CONF_DEVICE_CODE]
    device_files_absdir = os.path.join(COMPONENT_ABS_DIR, "codes", "climate")

    if not os.path.isdir(device_files_absdir):
        await hass.async_add_executor_job(os.makedirs, device_files_absdir)

    device_json_path = os.path.join(device_files_absdir, f"{device_code}.json")

    if not os.path.exists(device_json_path):
        _LOGGER.warning(
            "Couldn't find the device Json file. Trying to download it from the SmartIR repo."
        )
        try:
            source = (
                "https://raw.githubusercontent.com/smartHomeHub/SmartIR/master/"
                "codes/climate/{}.json"
            )
            await Helper.downloader(source.format(device_code), device_json_path)
        except Exception:  # noqa: BLE001
            _LOGGER.error(
                "Could not download device file %s. Place it manually in %s",
                device_code,
                device_files_absdir,
            )
            return

    def _load() -> dict:
        with open(device_json_path, encoding="utf-8") as file:
            return json.load(file)

    try:
        device_data = await hass.async_add_executor_job(_load)
    except Exception:  # noqa: BLE001
        _LOGGER.error("The device JSON file %s is invalid", device_json_path)
        return

    async_add_entities([SmartIRTuyaSyncClimate(hass, config, device_data)])


class SmartIRTuyaSyncClimate(SmartIRClimate):
    """SmartIR climate that mirrors the real state of a Tuya climate entity."""

    def __init__(self, hass, config, device_data):
        super().__init__(hass, config, device_data)
        self._tuya_entity = config[CONF_TUYA_ENTITY]
        self._sync_delay = config[CONF_SYNC_DELAY]
        # No dedicated temperature sensor configured -> use the Tuya one.
        self._use_tuya_temp = not self._temperature_sensor
        self._ignore_until = 0.0
        self._cancel_resync = None

    async def async_added_to_hass(self):
        await super().async_added_to_hass()

        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._tuya_entity], self._async_tuya_changed
            )
        )
        self.async_on_remove(self._async_cancel_resync)

        state = self.hass.states.get(self._tuya_entity)
        if state is not None:
            self._apply_tuya_state(state)

    # ---- IR sending: unchanged SmartIR behaviour, plus a short feedback grace period

    async def send_command(self):
        self._ignore_until = monotonic() + self._sync_delay
        try:
            await super().send_command()
        finally:
            # The AC needs a moment to apply the IR code and Tuya to report it.
            self._ignore_until = monotonic() + self._sync_delay
            self._async_cancel_resync()
            self._cancel_resync = async_call_later(
                self.hass, self._sync_delay, self._async_resync
            )

    # ---- State feedback

    @callback
    def _async_cancel_resync(self) -> None:
        if self._cancel_resync is not None:
            self._cancel_resync()
            self._cancel_resync = None

    @callback
    def _async_resync(self, _now) -> None:
        """Final alignment with Tuya once the grace period is over."""
        self._cancel_resync = None
        state = self.hass.states.get(self._tuya_entity)
        if state is not None:
            self._apply_tuya_state(state)
        self.async_write_ha_state()

    @callback
    def _async_tuya_changed(self, event) -> None:
        new_state = event.data["new_state"]
        if new_state is None:
            return
        if monotonic() < self._ignore_until:
            return  # the scheduled resync will pick up the final state
        self._apply_tuya_state(new_state)
        self.async_write_ha_state()

    def _match(self, value, candidates):
        """Case-insensitive match of a Tuya value in a SmartIR list (with aliases)."""
        if value is None or not candidates:
            return None
        value = str(value).lower()
        for wanted in (value, *FAN_ALIASES.get(value, ())):
            for candidate in candidates:
                if str(candidate).lower() == wanted:
                    return candidate
        return None

    @callback
    def _apply_tuya_state(self, state) -> None:
        """Copy the Tuya state into the SmartIR entity (never sends IR)."""
        if state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return  # keep the last known (optimistic) state

        attrs = state.attributes

        # hvac mode
        if state.state == HVACMode.OFF:
            new_mode = HVACMode.OFF
        elif state.state in self._operation_modes:
            new_mode = state.state
        elif self._hvac_mode != HVACMode.OFF:
            new_mode = self._hvac_mode  # Tuya mode without IR equivalent (heat_cool, fan_only)
        else:
            new_mode = self._last_on_operation or self._operation_modes[1]
        self._hvac_mode = new_mode
        if new_mode != HVACMode.OFF:
            self._last_on_operation = new_mode

        # target temperature
        try:
            target = float(attrs.get(ATTR_TEMPERATURE))
        except (TypeError, ValueError):
            target = None
        if target is not None and self._min_temperature <= target <= self._max_temperature:
            self._target_temperature = (
                round(target) if self._precision == PRECISION_WHOLE else round(target, 1)
            )

        # fan mode
        fan = self._match(attrs.get("fan_mode"), self._fan_modes)
        if fan is not None:
            self._current_fan_mode = fan

        # swing mode (only if the IR file has swing modes)
        if self._support_swing:
            swing = self._match(attrs.get("swing_mode"), self._swing_modes)
            if swing is not None:
                self._current_swing_mode = swing

        # current temperature
        if self._use_tuya_temp:
            try:
                self._current_temperature = float(attrs.get("current_temperature"))
            except (TypeError, ValueError):
                pass
