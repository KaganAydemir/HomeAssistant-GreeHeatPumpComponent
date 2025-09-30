"""
Gree Climate Entity for Home Assistant.

This module defines the climate (HVAC) unit for the Gree integration.
"""

# Standard library imports
import base64
import logging
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
    HP_MODES,
    DEFAULT_TARGET_TEMP_STEP,
    MIN_TEMP_C_B,
    MIN_TEMP_C_H,
    MIN_TEMP_F,
    MAX_TEMP_C_B,
    MAX_TEMP_C_H,
    MAX_TEMP_F,
    MODES_MAPPING,
    CONF_ENCRYPTION_KEY,
    CONF_UID,
    CONF_ENCRYPTION_VERSION,
    CONF_DISABLE_AVAILABLE_CHECK,
)
from .gree_protocol import Pad, FetchResult, GetDeviceKey, GetGCMCipher, EncryptGCM, GetDeviceKeyGCM
from .helpers import TempOffsetResolver, gree_f_to_c, gree_c_to_f, encode_temp_c, decode_temp_c

REQUIREMENTS = ["pycryptodome"]

_LOGGER = logging.getLogger(__name__)

SUPPORT_FLAGS = ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF


async def create_gree_device(hass, config):
    """Create a Gree device instance from config."""
    name = config.get(CONF_NAME, "Gree Climate")
    ip_addr = config.get(CONF_HOST)
    port = config.get(CONF_PORT, DEFAULT_PORT)
    mac_addr = config.get(CONF_MAC).encode().replace(b":", b"")

    chm = config.get(HP_MODES)
    hp_modes = [getattr(HPMode, mode.upper()) for mode in (chm if chm is not None else HP_MODES)]


    encryption_key = config.get(CONF_ENCRYPTION_KEY)
    uid = config.get(CONF_UID)
    encryption_version = config.get(CONF_ENCRYPTION_VERSION, 1)
    disable_available_check = config.get(CONF_DISABLE_AVAILABLE_CHECK, False)

    return GreeClimate(
        hass,
        name,
        ip_addr,
        port,
        mac_addr,
        hp_modes,
        encryption_version,
        disable_available_check,
        encryption_key,
        uid,
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
        hp_modes,
        encryption_version,
        disable_available_check,
        encryption_key=None,
        uid=None,
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
        self._target_radiator_temperature = None
        self._target_temperature = None
        # Initialize target temperature step with default value (will be overridden by number entity when available)
        self._target_temperature_step = DEFAULT_TARGET_TEMP_STEP
        # Device uses a combination of Celsius + a set bit for Fahrenheit, so the integration needs to be aware of the units.
        self._unit_of_measurement = hass.config.units.temperature_unit
        _LOGGER.info(f"{self._name}: Unit of measurement: {self._unit_of_measurement}")

        self._hp_modes = hp_modes
        self._hp_mode = HVACMode.OFF

        # Store for external temp sensor entity (set by sensor entity)

        # Keep unsub callbacks for deregistering listeners
        self._listeners: list = []



        self._current_boiler_temperature = None
        self._current_radiator_temperature = None
        self._current_tank_heater = None
        self._current_elec_heater_1 = None
        self._current_elec_heater_2 = None
        self._current_frost_protect = None

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

        self._hpOptions = {
            "Pow": None,
            "Mod": None,
            "CoWatOutTemSet": None,
            "HeWatOutTemSet": None,
            "WatBoxTemSet": None,
            "ColHtWter": None,
            "HetHtWter": None,
            "AllErr": None,
            "Quiet": None,
            "WatBoxExt": None,
            "Emegcy": None,
        }
        self._optionsToFetch = ["Pow", "Mod", "CoWatOutTemSet", "HeWatOutTemSet", "WatBoxTemSet", "ColHtWter", "HetHtWter", "AllErr", "Quiet", "WatBoxExt", "Emegcy", "AllInWatTemHi", "AllInWatTemLo", "AllOutWatTemHi", "AllOutWatTemLo", "HepOutWatTemHi", "HepOutWatTemLo", "WatBoxTemHi", "WatBoxTemLo", "AllInWatTemHi", "AllInWatTemLo", "AllOutWatTemHi", "AllOutWatTemLo", "HepOutWatTemHi", "HepOutWatTemLo", "WatBoxTemHi", "WatBoxTemLo", "RmoHomTemHi", "RmoHomTemLo", "WatBoxElcHeRunSta", "SyAnFroRunSta", "ElcHe1RunSta", "ElcHe2RunSta", "AnFrzzRunSta"]



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

    def SethpOptions(self, hpOptions, newOptionsToOverride, optionValuesToOverride=None):
        if optionValuesToOverride is not None:
            # Build a list of key-value pairs for a single log line
            settings = []
            for key in newOptionsToOverride:
                value = optionValuesToOverride[newOptionsToOverride.index(key)]
                settings.append(f"{key}={value}")
                hpOptions[key] = value
            _LOGGER.debug(f"{self._name}: Setting device options with retrieved values: {', '.join(settings)}")
        else:
            # Build a list of key-value pairs for a single log line
            settings = []
            for key, value in newOptionsToOverride.items():
                settings.append(f"{key}={value}")
                hpOptions[key] = value
            _LOGGER.debug(f"{self._name}: Overwriting device options with new settings: {', '.join(settings)}")
        return hpOptions

    async def SendStateToAc(self):
        opt_list = ["Pow", "Mod", "CoWatOutTemSet", "HeWatOutTemSet", "WatBoxTemSet", "Quiet"]

        # Collect values from _hpOptions
        p_values = [self._hpOptions.get(k) for k in opt_list]

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

    def UpdateHABoilerTargetTemperature(self):
        # Sync set temperature to HA..
        temp_c = self._hpOptions["WatBoxTemSet"]
        temp_f = gree_c_to_f(SetTem=self._hpOptions["WatBoxTemSet"], TemSet=1)

        if self._unit_of_measurement == "°C":
            display_temp = temp_c
        elif self._unit_of_measurement == "°F":
            display_temp = temp_f
        else:
            display_temp = temp_c  # default to deg c
            _LOGGER.error(f"{self._name}: Unknown unit of measurement: {self._unit_of_measurement}")

        self._target_temperature = display_temp

        _LOGGER.debug(f"{self._name}: Boiler Target temperature set to {self._target_temperature}{self._unit_of_measurement}")
    def UpdateHARadiatorTargetTemperature(self):
        # Sync set temperature to HA..
        temp_c = self._hpOptions["HeWatOutTemSet"]
        temp_f = gree_c_to_f(SetTem=self._hpOptions["HeWatOutTemSet"], TemSet=1)

        if self._unit_of_measurement == "°C":
            display_temp = temp_c
        elif self._unit_of_measurement == "°F":
            display_temp = temp_f
        else:
            display_temp = temp_c  # default to deg c
            _LOGGER.error(f"{self._name}: Unknown unit of measurement: {self._unit_of_measurement}")

        self._target_radiator_temperature = display_temp

        _LOGGER.debug(f"{self._name}: Radiator Target temperature set to {self._target_temperature}{self._unit_of_measurement}")

    def UpdateHAHpMode(self):
        # Sync current HVAC operation mode to HA
        if self._hpOptions["Pow"] == 0:
            self._hp_mode = HVACMode.OFF
        else:
            for key, value in MODES_MAPPING.get("Mod").items():
                if value == (self._hpOptions["Mod"]):
                    self._hp_mode = key
        _LOGGER.debug(f"{self._name}: Heat Pump mode updated to {self._hp_mode}")

    def UpdateHACurrentBoilerTemperature(self):
        _LOGGER.debug(f"{self._name}: Boiler temperature sensor reading: {self._hpOptions['WatBoxTemHi']*256 - self._hpOptions['WatBoxTemHi']/100 }")
        temp_c = self._hpOptions['WatBoxTemHi']*256 + self._hpOptions['WatBoxTemHi']/100
        temp_f = gree_c_to_f(SetTem=temp_c, TemRec=0)  # Convert to Fahrenheit using TemRec bit

        if self._unit_of_measurement == "°C":
            self._current_boiler_temperature = temp_c
        elif self._unit_of_measurement == "°F":
            self._current_boiler_temperature = temp_f
        else:
            _LOGGER.error("Unknown unit of measurement: %s" % self._unit_of_measurement)

        _LOGGER.debug(f"{self._name}: UpdateHACurrentTemperature: HA current boiler temperature set with device built-in temperature sensor state: {self._current_temperature}{self._unit_of_measurement}")

    def UpdateHARadiatorTemperature(self):
        # Update outside temperature from built-in AC outside temperature sensor if available

        _LOGGER.debug(f"{self._name}: UpdateHARadiatorTemperature: OutEnvTem: {self._hpOptions['AllOutWatTemHi']*256 + self._hpOptions['AllOutWatTemHi']/100}")
        # User hasn't set automatically, so try to determine the offset
        temp_c = self._process_temp_sensor(self._hpOptions['AllOutWatTemHi']*256 + self._hpOptions['AllOutWatTemHi']/100)
        _LOGGER.debug("method UpdateHARadiatorTemperature: User has not chosen an offset, using process_temp_sensor() to automatically determine offset.")


        temp_f = gree_c_to_f(SetTem=temp_c, TemRec=0)  # Convert to Fahrenheit using TemRec bit

        if self._unit_of_measurement == "°C":
            self._current_radiator_temperature = temp_c
        elif self._unit_of_measurement == "°F":
            self._current_radiator_temperature = temp_f
        else:
            _LOGGER.error("Unknown unit of measurement for outside temperature: %s" % self._unit_of_measurement)

        _LOGGER.debug(f"{self._name}: UpdateHARadiatorTemperature: HA radiator temperature set with device built-in outside temperature sensor state: {self._current_outside_temperature}{self._unit_of_measurement}")


    def UpdateHAStateToCurrentACState(self):
        self.UpdateHABoilerTargetTemperature()
        self.UpdateHARadiatorTargetTemperature()
        self.UpdateHAHpMode()
        self.UpdateHACurrentBoilerTemperature()
        self.UpdateHARadiatorTemperature()

    async def SyncState(self, hpOptions={}):
        # Fetch current settings from HVAC
        _LOGGER.debug(f"{self._name}: Starting device state sync")

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
            self._hpOptions = self.SethpOptions(self._hpOptions, optionsToFetch, currentValues)

            # Overwrite status with our choices
            if not (hpOptions == {}):
                self._hpOptions = self.SethpOptions(self._hpOptions, hpOptions)

            # If not the first (boot) run, update state towards the HVAC
            if not (self._firstTimeRun):
                if not (hpOptions == {}):
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
    def current_boiler_temperature(self):
        _LOGGER.debug(f"{self._name}: current_boiler_temperature() = {self._current_boiler_temperature}")
        # Return the current temperature.
        return self._current_temperature
    @property
    def current_radiator_temperature(self):
        _LOGGER.debug(f"{self._name}: current_boiler_temperature() = {self._current_radiator_temperature}")
        # Return the current temperature.
        return self._current_temperature
    @property
    def min_temp_h(self):
        if self._unit_of_measurement == "°C":
            MIN_TEMP = MIN_TEMP_C_H
        else:
            MIN_TEMP = MIN_TEMP_F

        _LOGGER.debug(f"{self._name}: min_temp() = {MIN_TEMP}")
        # Return the minimum temperature.
        return MIN_TEMP

    @property
    def max_temp_h(self):
        if self._unit_of_measurement == "°C":
            MAX_TEMP = MAX_TEMP_C_H
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
    def target_radiator_temperature(self):
        _LOGGER.debug(f"{self._name}: target_radiator_temperature() = {self._target_radiator_temperature}")
        # Return the temperature we try to reach.
        return self._target_radiator_temperature

    @property
    def target_temperature_step(self):
        _LOGGER.debug(f"{self._name}: target_temperature_step() = {self._target_temperature_step}")
        return self._target_temperature_step

    @property
    def hp_mode(self):
        _LOGGER.debug(f"{self._name}: hp_mode() = {self._hp_mode}")
        # Return current operation mode.
        return self._hp_mode



    @property
    def hp_modes(self):
        _LOGGER.debug(f"{self._name}: hp_modes() = {self._hp_modes}")
        # get the list of available operation modes.
        return self._hp_modes


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
    def boiler_temperature(self):
        _LOGGER.debug(f"{self._name}: boiler_temperature() = {self._current_boiler_temperature}")
        return self._current_boiler_temperature

    @property
    def radiator_temperature(self):
        _LOGGER.debug(f"{self._name}: radiator_temperature() = {self._current_radiator_temperature}")
        return self._current_radiator_temperature

    async def async_set_temperature(self, **kwargs):
        """Set new target temperature."""
        target_temperature = kwargs.get(ATTR_TEMPERATURE)
        if target_temperature is not None:
            # do nothing if temperature is none
            if not (self._hpOptions["Pow"] == 0):
                # todo F conversion
                await self.SyncState({"WatBoxTemSet": int(target_temperature)})
                _LOGGER.debug(f"{self._name}: async_set_temperature: Set Temp to {target_temperature}{self._unit_of_measurement}")
                self.async_write_ha_state()


    async def async_set_hp_mode(self, hp_mode):
        """Set new operation mode."""
        _LOGGER.info(f"{self._name}: async_set_hp_mode(): {hp_mode}")
        c = {}
        if hp_mode == HVACMode.OFF:
            c.update({"Pow": 0})
        else:
            mod = MODES_MAPPING.get("Mod").get(hp_mode)
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
