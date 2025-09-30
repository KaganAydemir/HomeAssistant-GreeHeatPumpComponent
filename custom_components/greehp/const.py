DOMAIN = "gree"

CONF_HP_MODES = "hp_modes"
CONF_ENCRYPTION_KEY = 'encryption_key'
CONF_UID = 'uid'
CONF_ENCRYPTION_VERSION = 'encryption_version'
CONF_DISABLE_AVAILABLE_CHECK  = 'disable_available_check'


DEFAULT_PORT = 7000
DEFAULT_TARGET_TEMP_STEP = 1

MIN_TEMP_C_B = 30 #Minimum boiler temp
MAX_TEMP_C_B = 60 #Maximum boiler temp

MIN_TEMP_C_H = 35 #Minimum heating temp
MAX_TEMP_C_H = 60 #Maximum heating temp

MIN_TEMP_F = 61
MAX_TEMP_F = 86


# Heat Pump modes
HP_MODES = ["off", "temp", "boiler", "temp1", "boiler_heating", "temp2"]
# Keys that can be updated via the options flow
OPTION_KEYS = {
    CONF_HP_MODES,
    CONF_DISABLE_AVAILABLE_CHECK,
}

MODES_MAPPING = {
  "Mod" : {
    "off" : 0,
    "temp" : 1,
    "boiler" : 2,
    "temp1" : 3,
    "boiler_heating" : 4,
    "temp2" : 5
  }
}