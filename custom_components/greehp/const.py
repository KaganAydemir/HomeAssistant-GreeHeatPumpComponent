DOMAIN = "gree"

CONF_HP_MODES = "hp_modes"
CONF_ENCRYPTION_KEY = 'encryption_key'
CONF_UID = 'uid'
CONF_ENCRYPTION_VERSION = 'encryption_version'
CONF_DISABLE_AVAILABLE_CHECK  = 'disable_available_check'


DEFAULT_PORT = 7000
DEFAULT_TARGET_TEMP_STEP = 1

MIN_TEMP_C = 10
MAX_TEMP_C = 60


MIN_TEMP_F = 61
MAX_TEMP_F = 86


# Heat Pump modes
DEFAULT_HVAC_MODES = ["auto", "cool", "dry", "fan_only", "heat", "off"]
HP_MODES = ["off", "temp", "boiler", "temp1", "boiler_heating", "temp2"]
# Keys that can be updated via the options flow
OPTION_KEYS = {
    CONF_HP_MODES,
    CONF_HVAC_MODES,
    CONF_DISABLE_AVAILABLE_CHECK,
}

MODES_MAPPING = {
    "Mod": {
        "auto": 0,
        "cool": 1,
        "dry": 2,
        "fan_only": 3,
        "heat": 4
    }
#   "Mod" : {
#     "off" : 0,
#     "temp" : 1,
#     "boiler" : 2,
#     "temp1" : 3,
#     "boiler_heating" : 4,
#     "temp2" : 5
#   }
}