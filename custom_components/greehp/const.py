DOMAIN = "greehp"

CONF_HVAC_MODES = "hvac_modes"
CONF_ENCRYPTION_KEY = 'encryption_key'
CONF_UID = 'uid'
CONF_ENCRYPTION_VERSION = 'encryption_version'
CONF_DISABLE_AVAILABLE_CHECK  = 'disable_available_check'
CONF_TEMP_SENSOR_OFFSET = 'temp_sensor_offset'

DEFAULT_PORT = 7000
DEFAULT_TARGET_TEMP_STEP = 1

MIN_TEMP_C = 15
MAX_TEMP_C = 65

MIN_TEMP_F = 59
MAX_TEMP_F = 149

TEMSEN_OFFSET = 40

# HVAC modes - these come from Home Assistant and are standard
DEFAULT_HVAC_MODES = ["auto", "cool", "dry", "fan_only", "heat", "off"]
PRESET_MODES = ["Boyler", "Boyler ve Kalorifer"]
# Keys that can be updated via the options flow
OPTION_KEYS = {
    CONF_HVAC_MODES,
    CONF_DISABLE_AVAILABLE_CHECK,
    CONF_TEMP_SENSOR_OFFSET,
}

MODES_MAPPING = {
  "Mod" : {
    "auto" : 0,
    "cool" : 1,
    "dry" : 2,
    "fan_only" : 3,
    "heat" : 4
  }
}
