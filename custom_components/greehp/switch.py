"""Support for Gree switches."""

from __future__ import annotations

# Standard library imports
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# Home Assistant imports
from homeassistant.components.climate import HVACMode
from homeassistant.components.switch import (
    SwitchEntity,
    SwitchEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

# Local imports
from .entity import GreeEntity, GreeEntityDescription

_LOGGER = logging.getLogger(__name__)


@dataclass
class GreeSwitchEntityDescription(GreeEntityDescription, SwitchEntityDescription):
    """Describes Gree Switch entity."""

    set_fn: Callable[[object, bool], None] = None
    restore_state: bool = False
    """Whether to restore the state of the switch on startup."""

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Gree switch based on a config entry."""
    async_add_entities(GreeSwitchEntity(hass, entry, description) for description in SWITCHES)


class GreeSwitchEntity(GreeEntity, SwitchEntity, RestoreEntity):
    """Defines a Gree Switch entity."""

    entity_description: GreeSwitchEntityDescription

    def __init__(
        self,
        hass,
        entry,
        description: GreeSwitchEntityDescription,
    ) -> None:
        super().__init__(hass, entry, description)
        self._attr_is_on = bool(self.native_value)
        self._restored = False

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        # Restore state if applicable
        if self.entity_description.restore_state:
            last_state = await self.async_get_last_state()
            if last_state is not None:
                value = last_state.state == "on"
                await self.entity_description.set_fn(self._device, value)
                self._attr_is_on = value
                self._restored = True

    @property
    def native_value(self):
        if self.entity_description.restore_state:
            return getattr(self, "_attr_is_on", False)
        return super().native_value

    @property
    def is_on(self) -> bool:
        return bool(self.native_value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the switch."""
        if not self.available:
            raise HomeAssistantError("Entity unavailable")

        if self.entity_description.set_fn:
            await self.entity_description.set_fn(self._device, True)

        if self.entity_description.restore_state:
            self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the switch."""
        if not self.available:
            raise HomeAssistantError("Entity unavailable")

        if self.entity_description.set_fn:
            await self.entity_description.set_fn(self._device, False)

        if self.entity_description.restore_state:
            self._attr_is_on = False
        self.async_write_ha_state()
