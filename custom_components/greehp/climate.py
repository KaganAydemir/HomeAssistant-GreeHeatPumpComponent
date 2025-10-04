"""
Gree Climate Entity for Home Assistant.

This module defines the climate (HVAC) unit for the Gree integration.
"""

# Standard library imports
import base64
import logging
import math
from datetime import timedelta

# Third-party imports
try:
    import simplejson
except ImportError:
    import json as simplejson
from Crypto.Cipher import AES

# Home Assistant imports
from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACMode
from homeassistant.const import (
    ATTR_TEMPERATURE,
    ATTR_UNIT_OF_MEASUREMENT,
    CONF_HOST,
    CONF_MAC,
    CONF_NAME,
    CONF_PORT,
)
from homeassistant.helpers.device_registry import DeviceInfo

# Local imports
from .const import (
    DOMAIN,
    DEFAULT_PORT,
    DEFAULT_HVAC_MODES,
    PRESET_MODES,
    DEFAULT_TARGET_TEMP_STEP,
    MIN_TEMP_C,
    MIN_TEMP_F,
    MAX_TEMP_C,
    MAX_TEMP_F,
    MODES_MAPPING,
    TEMSEN_OFFSET,
    CONF_HVAC_MODES,
    CONF_ENCRYPTION_KEY,
    CONF_UID,
    CONF_ENCRYPTION_VERSION,
    CONF_DISABLE_AVAILABLE_CHECK,
    CONF_TEMP_SENSOR_OFFSET,
)
from .gree_protocol import Pad, FetchResult, GetDeviceKey, GetGCMCipher, EncryptGCM, GetDeviceKeyGCM
from .helpers import TempOffsetResolver, gree_f_to_c, gree_c_to_f, encode_temp_c, decode_temp_c

REQUIREMENTS = ["pycryptodome"]

_LOGGER = logging.getLogger(__name__)

SUPPORT_FLAGS = ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF | ClimateEntityFeature.PRESET_MODE

async def create_gree_device(hass, config):
    """Create a Gree device instance from config."""
    name = config.get(CONF_NAME, "Gree Climate")
    ip_addr = config.get(CONF_HOST)
    port = config.get(CONF_PORT, DEFAULT_PORT)
    mac_addr = config.get(CONF_MAC).encode().replace(b":", b"")

    chm = config.get(CONF_HVAC_MODES)
    hvac_modes = [getattr(HVACMode, mode.upper()) for mode in (chm if chm is not None else DEFAULT_HVAC_MODES)]

    #cfm = config.get(CONF_FAN_MODES)
    #fan_modes = cfm if cfm is not None else DEFAULT_FAN_MODES
    #csm = config.get(CONF_SWING_MODES)
    #swing_modes = csm if csm is not None else DEFAULT_SWING_MODES
    #cshm = config.get(CONF_SWING_HORIZONTAL_MODES)
    #swing_horizontal_modes = cshm if cshm is not None else DEFAULT_SWING_HORIZONTAL_MODES
    encryption_key = config.get(CONF_ENCRYPTION_KEY)
    uid = config.get(CONF_UID)
    encryption_version = config.get(CONF_ENCRYPTION_VERSION, 1)
    disable_available_check = config.get(CONF_DISABLE_AVAILABLE_CHECK, False)
    temp_sensor_offset = config.get(CONF_TEMP_SENSOR_OFFSET)

    return GreeClimate(
        hass,
        name,
        ip_addr,
        port,
        mac_addr,
        hvac_modes,
        #fan_modes,
        #swing_modes,
        #swing_horizontal_modes,
        encryption_version,
        disable_available_check,
        encryption_key,
        uid,
        temp_sensor_offset,
    )


# from the remote control and gree app

# update() interval
SCAN_INTERVAL = timedelta(seconds=60)


async def async_setup_entry(hass, entry, async_add_devices):
    """Set up Gree climate from a config entry."""
    # Get the device that was created in __init__.py
    entry_data = hass.data[DOMAIN][entry.entry_id]
    device = entry_data["device"]

    async_add_devices([device])


async def async_unload_entry(hass, entry):
    """Unload a config entry."""
    return True


class GreeClimate(ClimateEntity):
    # Language is retrieved from translation key
    _attr_translation_key = "gree"

    def __init__(
        self,
        hass,
        name,
        ip_addr,
        port,
        mac_addr,
        hvac_modes,
        encryption_version,
        disable_available_check,
        encryption_key=None,
        uid=None,
        temp_sensor_offset=None,
    ):
        _LOGGER.info(f"{name}: Initializing Gree climate device")

        self.hass = hass
        self._name = name
        self._ip_addr = ip_addr
        self._port = port
        mac_addr_str = mac_addr.decode("utf-8").lower()
        if "@" in mac_addr_str:
            self._sub_mac_addr, self._mac_addr = mac_addr_str.split("@", 1)
        else:
            self._sub_mac_addr = self._mac_addr = mac_addr_str
        self._unique_id = f"{DOMAIN}_{self._sub_mac_addr}"
        self._device_online = None
        self._disable_available_check = disable_available_check

        self._target_temperature = None
        # Initialize target temperature step with default value (will be overridden by number entity when available)
        self._target_temperature_step = DEFAULT_TARGET_TEMP_STEP
        # Device uses a combination of Celsius + a set bit for Fahrenheit, so the integration needs to be aware of the units.
        self._unit_of_measurement = hass.config.units.temperature_unit
        _LOGGER.info(f"{self._name}: Unit of measurement: {self._unit_of_measurement}")

        self._hvac_modes = hvac_modes
        self._hvac_mode = HVACMode.OFF
        self._heating_temperature = None

        self._temp_sensor_offset = temp_sensor_offset

        # Store for external temp sensor entity (set by sensor entity)
        self._external_temperature_sensor = None

        # Keep unsub callbacks for deregistering listeners
        self._listeners: list = []

        self._has_temp_sensor = None
        self._has_outside_temp_sensor = None
        self._current_temperature = None
        self._firstTimeRun = True

        self._enable_turn_on_off_backwards_compatibility = False

        self.encryption_version = encryption_version
        self.CIPHER = None

        if encryption_key:
            _LOGGER.info(f"{self._name}: Using configured encryption key: {encryption_key}")
            self._encryption_key = encryption_key.encode("utf8")
            if encryption_version == 1:
                # Cipher to use to encrypt/decrypt
                self.CIPHER = AES.new(self._encryption_key, AES.MODE_ECB)
            elif self.encryption_version != 2:
                _LOGGER.error(f"{self._name}: Encryption version {self.encryption_version} is not implemented")
        else:
            self._encryption_key = None

        if uid:
            self._uid = uid
        else:
            self._uid = 0

        self._acOptions = {
            "Pow": None,
            "Mod": None,
            "WatBoxTemSet": None,
            "WatBoxTemHi": None,
            "WatBoxTemLo": None,
        }
        self._optionsToFetch = ["Pow", "Mod", "WatBoxTemSet", "HeWatOutTemSet", "WatBoxTemHi","WatBoxTemLo"]

        # Initialize auto switches


        # Initialize beeper control

        # helper method to determine TemSen offset
        self._process_temp_sensor = TempOffsetResolver()

    async def GreeGetValues(self, propertyNames):
        plaintext = '{"cols":' + simplejson.dumps(propertyNames) + ',"mac":"' + str(self._sub_mac_addr) + '","t":"status"}'
        if self.encryption_version == 1:
            cipher = self.CIPHER
            jsonPayloadToSend = '{"cid":"app","i":0,"pack":"' + base64.b64encode(cipher.encrypt(Pad(plaintext).encode("utf8"))).decode("utf-8") + '","t":"pack","tcid":"' + str(self._mac_addr) + '","uid":{}'.format(self._uid) + "}"
        elif self.encryption_version == 2:
            pack, tag = EncryptGCM(self._encryption_key, plaintext)
            jsonPayloadToSend = '{"cid":"app","i":0,"pack":"' + pack + '","t":"pack","tcid":"' + str(self._mac_addr) + '","uid":{}'.format(self._uid) + ',"tag" : "' + tag + '"}'
            cipher = GetGCMCipher(self._encryption_key)
        result = await FetchResult(cipher, self._ip_addr, self._port, jsonPayloadToSend, encryption_version=self.encryption_version)
        return result["dat"][0] if len(result["dat"]) == 1 else result["dat"]

    def SetAcOptions(self, acOptions, newOptionsToOverride, optionValuesToOverride=None):
        if optionValuesToOverride is not None:
            # Build a list of key-value pairs for a single log line
            settings = []
            for key in newOptionsToOverride:
                value = optionValuesToOverride[newOptionsToOverride.index(key)]
                settings.append(f"{key}={value}")
                acOptions[key] = value
            _LOGGER.debug(f"{self._name}: Setting device options with retrieved values: {', '.join(settings)}")
        else:
            # Build a list of key-value pairs for a single log line
            settings = []
            for key, value in newOptionsToOverride.items():
                settings.append(f"{key}={value}")
                acOptions[key] = value
            _LOGGER.debug(f"{self._name}: Overwriting device options with new settings: {', '.join(settings)}")
        return acOptions

    async def SendStateToAc(self):
        opt_list = ["Pow", "Mod", "WatBoxTemSet", "HeWatOutTemSet"]

        # Collect values from _acOptions
        p_values = [self._acOptions.get(k) for k in opt_list]

        # Filter out empty ones
        filtered_opt = []
        filtered_p = []
        for name, val in zip(opt_list, p_values):
            if val not in ("", None):
                filtered_opt.append(f'"{name}"')
                filtered_p.append(str(val))

        statePackJson = '{"opt":[' + ",".join(filtered_opt) + '],"p":[' + ",".join(filtered_p) + '],"t":"cmd","sub":"' + self._sub_mac_addr + '"}'

        if self.encryption_version == 1:
            cipher = self.CIPHER
            sentJsonPayload = '{"cid":"app","i":0,"pack":"' + base64.b64encode(cipher.encrypt(Pad(statePackJson).encode("utf8"))).decode("utf-8") + '","t":"pack","tcid":"' + str(self._mac_addr) + '","uid":{}'.format(self._uid) + "}"
        elif self.encryption_version == 2:
            pack, tag = EncryptGCM(self._encryption_key, statePackJson)
            sentJsonPayload = '{"cid":"app","i":0,"pack":"' + pack + '","t":"pack","tcid":"' + str(self._mac_addr) + '","uid":{}'.format(self._uid) + ',"tag":"' + tag + '"}'
            cipher = GetGCMCipher(self._encryption_key)
        result = await FetchResult(cipher, self._ip_addr, self._port, sentJsonPayload, encryption_version=self.encryption_version)
        _LOGGER.debug(f"{self._name}: Command sent successfully: {str(result)}")

    def UpdateHATargetTemperature(self):

        temp_c = self._acOptions["WatBoxTemSet"]
        temp_f = math.floor((self._acOptions["WatBoxTemSet"] * 9/5) + 32)

        if self._unit_of_measurement == "°C":
            display_temp = temp_c
        elif self._unit_of_measurement == "°F":
            display_temp = temp_f
        else:
            display_temp = temp_c  # default to deg c
            _LOGGER.error(f"{self._name}: Unknown unit of measurement: {self._unit_of_measurement}")

        self._target_temperature = display_temp
        _LOGGER.debug(f"{self._name}: Target temperature set to {self._target_temperature}{self._unit_of_measurement}")

    def UpdateHAHeatingTemperature(self):

        temp_c = self._acOptions["HeWatOutTemSet"]
        temp_f = math.floor((self._acOptions["HeWatOutTemSet"] * 9/5) + 32)

        if self._unit_of_measurement == "°C":
            heating_temperature = temp_c
        elif self._unit_of_measurement == "°F":
            heating_temperature = temp_f
        else:
            heating_temperature = temp_c  # default to deg c
            _LOGGER.error(f"{self._name}: Unknown unit of measurement: {self._unit_of_measurement}")

        self._heating_temperature = heating_temperature

        _LOGGER.debug(f"{self._name}: Target temperature set to {self._heating_temperature}{self._unit_of_measurement}")

    def UpdateHAHvacMode(self):
        # Sync current HVAC operation mode to HA
        if self._acOptions["Pow"] == 0:
            self._hvac_mode = HVACMode.OFF
        else:
            for key, value in MODES_MAPPING.get("Mod").items():
                if value == (self._acOptions["Mod"]):
                    self._hvac_mode = key
        _LOGGER.debug(f"{self._name}: HVAC mode updated to {self._hvac_mode}")

    def UpdateHACurrentTemperature(self):
        # Use external temperature sensor if available
        if self._external_temperature_sensor:
            # Use external temperature sensor
            external_sensor_state = self.hass.states.get(self._external_temperature_sensor)
            if external_sensor_state and external_sensor_state.state not in ("unknown", "unavailable"):
                try:
                    unit = external_sensor_state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
                    _LOGGER.debug(f"{self._name}: Using external temperature sensor {self._external_temperature_sensor}: {external_sensor_state.state}{unit}")
                    self._current_temperature = self.hass.config.units.temperature(float(external_sensor_state.state), unit)
                    _LOGGER.debug(f"{self._name}: Current temperature from external sensor: {self._current_temperature}{self._unit_of_measurement}")
                    return
                except (ValueError, TypeError) as ex:
                    _LOGGER.error(f"{self._name}: Unable to update from external temp sensor {self._external_temperature_sensor}: {ex}")

        # Use built-in AC temperature sensor if available
        if self._has_temp_sensor:
            _LOGGER.debug(f"{self._name}: Built-in temperature sensor reading: {(self._acOptions["WatBoxTemHi"] - 100) + (self._acOptions["WatBoxTemLo"]/10)}")


            temp_c = (self._acOptions["WatBoxTemHi"] - 100) + (self._acOptions["WatBoxTemLo"]/10)

            temp_f = gree_c_to_f(SetTem=temp_c, TemRec=0)  # Convert to Fahrenheit using TemRec bit

            if self._unit_of_measurement == "°C":
                self._current_temperature = temp_c
            elif self._unit_of_measurement == "°F":
                self._current_temperature = temp_f
            else:
                _LOGGER.error("Unknown unit of measurement: %s" % self._unit_of_measurement)

            _LOGGER.debug(f"{self._name}: UpdateHACurrentTemperature: HA current temperature set with device built-in temperature sensor state: {self._current_temperature}{self._unit_of_measurement}")

    def UpdateHAOutsideTemperature(self):
        # Update outside temperature from built-in AC outside temperature sensor if available
        if self._has_outside_temp_sensor:
            _LOGGER.debug(f"{self._name}: UpdateHAOutsideTemperature: OutEnvTem: {self._acOptions['OutEnvTem']}")

            if self._temp_sensor_offset is None:  # user hasn't chosen an offset
                # User hasn't set automatically, so try to determine the offset
                temp_c = self._process_temp_sensor(self._acOptions["OutEnvTem"])
                _LOGGER.debug("method UpdateHAOutsideTemperature: User has not chosen an offset, using process_temp_sensor() to automatically determine offset.")
            else:
                # User set
                if self._temp_sensor_offset is True:
                    temp_c = self._acOptions["OutEnvTem"] - TEMSEN_OFFSET
                elif self._temp_sensor_offset is False:
                    temp_c = self._acOptions["OutEnvTem"]

                _LOGGER.debug(f"method UpdateHAOutsideTemperature: User has chosen an offset ({self._temp_sensor_offset})")

            temp_f = gree_c_to_f(SetTem=temp_c, TemRec=0)  # Convert to Fahrenheit using TemRec bit

            if self._unit_of_measurement == "°C":
                self._current_outside_temperature = temp_c
            elif self._unit_of_measurement == "°F":
                self._current_outside_temperature = temp_f
            else:
                _LOGGER.error("Unknown unit of measurement for outside temperature: %s" % self._unit_of_measurement)

            _LOGGER.debug(f"{self._name}: UpdateHAOutsideTemperature: HA outside temperature set with device built-in outside temperature sensor state: {self._current_outside_temperature}{self._unit_of_measurement}")

    def UpdateHAStateToCurrentACState(self):
        self.UpdateHATargetTemperature()
        self.UpdateHAHvacMode()
        self.UpdateHACurrentTemperature()
        self.UpdateHAOutsideTemperature()
        self.UpdateHAHeatingTemperature()

    async def SyncState(self, acOptions={}):
        # Fetch current settings from HVAC
        _LOGGER.debug(f"{self._name}: Starting device state sync")

        if self._has_temp_sensor is None:
            _LOGGER.debug("Attempt to check whether device has an built-in temperature sensor")
            try:
                temp_sensor = await self.GreeGetValues(["WatBoxTemHi"])
            except Exception:
                _LOGGER.debug("Could not determine whether device has an built-in temperature sensor. Retrying at next update()")
            else:
                if temp_sensor:
                    self._has_temp_sensor = True
                    _LOGGER.debug("Device has an built-in temperature sensor")
                else:
                    self._has_temp_sensor = False
                    _LOGGER.debug("Device has no built-in temperature sensor")

        # Check if device has outside temperature sensor
        if self._has_outside_temp_sensor is None:
            _LOGGER.debug("Attempt to check whether device has an outside temperature sensor")
            try:
                outside_temp_sensor = await self.GreeGetValues(["OutEnvTem"])
            except Exception:
                _LOGGER.debug("Could not determine whether device has an outside temperature sensor. Retrying at next update()")
            else:
                if outside_temp_sensor:
                    self._has_outside_temp_sensor = True
                    self._acOptions.update({"OutEnvTem": None})
                    self._optionsToFetch.append("OutEnvTem")
                    _LOGGER.debug("Device has an outside temperature sensor")
                else:
                    self._has_outside_temp_sensor = False
                    _LOGGER.debug("Device has no outside temperature sensor")

        optionsToFetch = self._optionsToFetch

        try:
            currentValues = await self.GreeGetValues(optionsToFetch)
        except Exception as e:
            _LOGGER.warning(f"{self._name}: Failed to communicate with device {self._ip_addr}:{self._port}: {str(e)}")
            if not self._disable_available_check:
                _LOGGER.info(f"{self._name}: Device marked offline after failed communication")
                self._device_online = False
        else:
            if not self._disable_available_check:
                if not self._device_online:
                    self._device_online = True
            # Set latest status from device
            self._acOptions = self.SetAcOptions(self._acOptions, optionsToFetch, currentValues)

            # Overwrite status with our choices
            if not (acOptions == {}):
                self._acOptions = self.SetAcOptions(self._acOptions, acOptions)

            # If not the first (boot) run, update state towards the HVAC
            if not (self._firstTimeRun):
                if not (acOptions == {}):
                    # loop used to send changed settings from HA to HVAC
                    try:
                        await self.SendStateToAc()
                    except Exception as e:
                        _LOGGER.warning(f"{self._name}: Failed to send state to device {self._ip_addr}:{self._port}: {str(e)}")
                        # Mark device as offline if communication fails
                        if not self._disable_available_check:
                            _LOGGER.info(f"{self._name}: Device marked offline after failed send attempt")
                            self._device_online = False
            else:
                # loop used once for Gree Climate initialisation only
                self._firstTimeRun = False

            # Update HA state to current HVAC state
            self.UpdateHAStateToCurrentACState()

            _LOGGER.debug(f"{self._name}: Finished device state sync")

    @property
    def should_poll(self):
        _LOGGER.debug("should_poll()")
        # Return the polling state.
        return True

    @property
    def available(self):
        if self._disable_available_check:
            return True
        else:
            if self._device_online:
                _LOGGER.debug("available(): Device is online")
                return True
            else:
                _LOGGER.debug("available(): Device is offline")
                return False

    async def async_update(self):
        """Retrieve latest state."""
        _LOGGER.debug("async_update()")
        if not self._encryption_key:
            if self.encryption_version == 1:
                key = await GetDeviceKey(self._mac_addr, self._ip_addr, self._port)
                if key:
                    self._encryption_key = key
                    self.CIPHER = AES.new(self._encryption_key, AES.MODE_ECB)
                    await self.SyncState()
            elif self.encryption_version == 2:
                key = await GetDeviceKeyGCM(self._mac_addr, self._ip_addr, self._port)
                if key:
                    self._encryption_key = key
                    self.CIPHER = GetGCMCipher(self._encryption_key)
                    await self.SyncState()
            else:
                _LOGGER.error("Encryption version %s is not implemented." % self.encryption_version)
        else:
            await self.SyncState()

    @property
    def name(self):
        _LOGGER.debug(f"{self._name}: name() = {self._name}")
        # Return the name of the climate device.
        return self._name

    @property
    def temperature_unit(self):
        _LOGGER.debug(f"{self._name}: temperature_unit() = {self._unit_of_measurement}")
        # Return the unit of measurement.
        return self._unit_of_measurement

    @property
    def current_temperature(self):
        _LOGGER.debug(f"{self._name}: current_temperature() = {self._current_temperature}")
        # Return the current temperature.
        return self._current_temperature

    @property
    def min_temp(self):
        if self._unit_of_measurement == "°C":
            MIN_TEMP = MIN_TEMP_C
        else:
            MIN_TEMP = MIN_TEMP_F

        _LOGGER.debug(f"{self._name}: min_temp() = {MIN_TEMP}")
        # Return the minimum temperature.
        return MIN_TEMP

    @property
    def max_temp(self):
        if self._unit_of_measurement == "°C":
            MAX_TEMP = MAX_TEMP_C
        else:
            MAX_TEMP = MAX_TEMP_F

        _LOGGER.debug(f"{self._name}: max_temp() = {MAX_TEMP}")
        # Return the maximum temperature.
        return MAX_TEMP

    @property
    def target_temperature(self):
        _LOGGER.debug(f"{self._name}: target_temperature() = {self._target_temperature}")
        # Return the temperature we try to reach.
        return self._target_temperature

    @property
    def target_temperature_step(self):
        _LOGGER.debug(f"{self._name}: target_temperature_step() = {self._target_temperature_step}")
        return self._target_temperature_step

    @property
    def hvac_mode(self):
        _LOGGER.debug(f"{self._name}: hvac_mode() = {self._hvac_mode}")
        # Return current operation mode ie. heat, cool, idle.
        return self._hvac_mode

    @property
    def hvac_modes(self):
        _LOGGER.debug(f"{self._name}: hvac_modes() = {self._hvac_modes}")
        # get the list of available operation modes.
        #return self._hvac_modes
        return []

    @property
    def supported_features(self):
        sf = SUPPORT_FLAGS
        _LOGGER.debug(f"{self._name}: supported_features() = {sf}")
        # Return the list of supported features.
        return sf
    @property
    def unique_id(self):
        # Return unique_id
        return self._unique_id

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._mac_addr)},
            name=self._name,
            manufacturer="Gree",
        )

    @property
    def outside_temperature(self):
        """Return the outside temperature if available."""
        if self._has_outside_temp_sensor:
            _LOGGER.debug(f"{self._name}: outside_temperature() = {self._current_outside_temperature}")
            return self._current_outside_temperature
        return None

    @property
    def heating_temperature(self):
        """Return the outside temperature if available."""
        _LOGGER.debug(f"{self._name}: heating_temperature() = {self._heating_temperature}")
        return self._heating_temperature

    @property
    def preset_modes(self):
        """Return the available preset modes."""
        return PRESET_MODES

    @property
    def preset_mode(self):
        """Return the currently active preset mode."""
        # Map from AC state to preset
        if self._acOptions["Mod"] == 2:  # Example: Boiler
            return "Boyler"
        elif self._acOptions["Mod"] == 4:  # Example: Boiler and Heating
            return "Boyler ve Kalorifer"
        return None
    @property
    def extra_state_attributes(self):
        """Return additional state attributes."""
        attributes = {}

        # if self.outside_temperature is not None:
        #     attributes["outside_temperature"] = self.outside_temperature
        #     attributes["outside_temperature_unit"] = self._unit_of_measurement

        # if self.room_humidity is not None:
        #     attributes["room_humidity"] = self.room_humidity
        #     attributes["room_humidity_unit"] = "%"

        return attributes if attributes else None

    async def async_set_temperature(self, **kwargs):
        """Set new target temperature."""
        target_temperature = kwargs.get(ATTR_TEMPERATURE)
        if target_temperature is not None:
            # do nothing if temperature is none
            if not (self._acOptions["Pow"] == 0):
                # do nothing if HVAC is switched off

                if self._unit_of_measurement == "°C":
                    WatBoxTemSet = target_temperature  # takes care of 1/2 degrees
                elif self._unit_of_measurement == "°F":
                    WatBoxTemSet = math.floor((target_temperature * 9/5) + 32)
                else:
                    _LOGGER.error("Unable to set temperature. Units not set to °C or °F")
                    return

                await self.SyncState({"WatBoxTemSet": int(WatBoxTemSet)})
                _LOGGER.debug(f"{self._name}: async_set_temperature: Set Temp to {target_temperature}{self._unit_of_measurement} ->  SyncState with SetTem={WatBoxTemSet}")

                self.async_write_ha_state()

    async def async_set_heating_temperature(self, **kwargs):
        """Set new heating temperature."""
        heating_temperature = kwargs.get(ATTR_TEMPERATURE)
        if heating_temperature is not None:
            # do nothing if temperature is none
            if not (self._acOptions["Pow"] == 0):
                # do nothing if HVAC is switched off

                if self._unit_of_measurement == "°C":
                    HeWatOutTemSet = heating_temperature  # takes care of 1/2 degrees
                elif self._unit_of_measurement == "°F":
                    HeWatOutTemSet = math.floor((heating_temperature * 9/5) + 32)
                else:
                    _LOGGER.error("Unable to set temperature. Units not set to °C or °F")
                    return

                await self.SyncState({"HeWatOutTemSet": int(HeWatOutTemSet)})
                _LOGGER.debug(f"{self._name}: async_set_temperature: Set Temp to {heating_temperature}{self._unit_of_measurement} ->  SyncState with SetTem={HeWatOutTemSet}")

                self.async_write_ha_state()

    async def async_set_preset_mode(self, preset_mode: str):
        """Set the selected preset mode."""
        if preset_mode not in PRESET_MODES:
            _LOGGER.error(f"Invalid preset mode: {preset_mode}")
            return

        # Map your presets to Gree device commands here
        c = {}  # ensure AC is on
        if preset_mode == "Boyler":
            c.update({"Pow": 1, "Mod": 2})
        elif preset_mode == "Boyler ve Kalorifer":
            c.update({"Pow": 1, "Mod": 4})
        await self.SyncState(c)
        self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode):
        """Set new operation mode."""
        _LOGGER.info(f"{self._name}: async_set_hvac_mode(): {hvac_mode}")
        c = {}
        if hvac_mode == HVACMode.OFF:
            c.update({"Pow": 0})
        else:
            mod = MODES_MAPPING.get("Mod").get(hvac_mode)
            c.update({"Pow": 1, "Mod": mod})
        await self.SyncState(c)
        self.async_write_ha_state()

    async def async_turn_on(self):
        """Turn on."""
        _LOGGER.info("async_turn_on(): ")
        # Turn on.
        c = {"Pow": 1}
        await self.SyncState(c)
        self.async_write_ha_state()

    async def async_turn_off(self):
        """Turn off."""
        _LOGGER.info("async_turn_off(): ")
        # Turn off.
        c = {"Pow": 0}
        await self.SyncState(c)
        self.async_write_ha_state()

    async def async_added_to_hass(self):
        _LOGGER.info("Gree climate device added to hass()")
        await self.async_update()

    async def async_will_remove_from_hass(self) -> None:
        """Clean up when entity is removed."""
        for name, entity_id, unsub in self._listeners:
            _LOGGER.debug("Deregistering %s listener for %s", name, entity_id)
            unsub()
        self._listeners.clear()
