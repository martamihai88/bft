"""Platform for the BFT cover component."""
import logging

import requests
import voluptuous as vol

import homeassistant.helpers.config_validation as cv
from homeassistant.components.cover import CoverDevice, PLATFORM_SCHEMA
from homeassistant.helpers.event import track_utc_time_change
from homeassistant.const import (
    CONF_DEVICE,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_ACCESS_TOKEN,
    CONF_NAME,
    STATE_CLOSED,
    STATE_OPEN,
    CONF_COVERS,
)

_LOGGER = logging.getLogger(__name__)

ATTR_AVAILABLE = "available"
ATTR_TIME_IN_STATE = "time_in_state"

DEFAULT_NAME = "BFT"

STATE_MOVING = "moving"
STATE_OFFLINE = "offline"
STATE_STOPPED = "stopped"

STATES_MAP = {
    "open": STATE_OPEN,
    "moving": STATE_MOVING,
    "closed": STATE_CLOSED,
    "stopped": STATE_STOPPED,
}

COVER_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_ACCESS_TOKEN): cv.string,
        vol.Optional(CONF_DEVICE): cv.string,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Optional(CONF_PASSWORD): cv.string,
        vol.Optional(CONF_USERNAME): cv.string,
    }
)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {vol.Required(CONF_COVERS): cv.schema_with_slug_keys(COVER_SCHEMA)}
)


def setup_platform(hass, config, add_entities, discovery_info=None):
    """Set up the BFT covers."""
    covers = []
    devices = config.get(CONF_COVERS)

    for device_name, device_config in devices.items():
        args = {
            "name": device_config.get(CONF_NAME),
            "device": device_config.get(CONF_DEVICE),
            "username": device_config.get(CONF_USERNAME),
            "password": device_config.get(CONF_PASSWORD),
            "access_token": device_config.get(CONF_ACCESS_TOKEN),
        }

        covers.append(BftCover(hass, args))

    add_entities(covers)


class BftCover(CoverDevice):
    """Representation of a BFT cover."""

    def __init__(self, hass, args):
        """Initialize the cover."""
        self.particle_url = "https://ucontrol-api.bft-automation.com"
        self.dispatcher_api_url = (
            "https://ucontrol-dispatcher.bft-automation.com/automations"
        )
        self.hass = hass
        self._name = args["name"]
        self.device_name = args["device"]
        self.device_id = None
        self.access_token = args["access_token"]
        self.obtained_token = False
        self._username = args["username"]
        self._password = args["password"]
        self._state = None
        self.time_in_state = None
        self._unsub_listener_cover = None
        self._available = True

        if self.access_token is None:
            self.access_token = self.get_token()
            self._obtained_token = True

        if self.device_id is None:
            self.device_id = self.get_device_id()

        try:
            self.update()
        except requests.exceptions.ConnectionError as ex:
            _LOGGER.error("Unable to connect to server: %(reason)s", dict(reason=ex))
            self._state = STATE_OFFLINE
            self._available = False
            self._name = DEFAULT_NAME
        except KeyError:
            _LOGGER.warning(
                "BFT device %(device)s seems to be offline", dict(device=self.device_id)
            )
            self._name = DEFAULT_NAME
            self._state = STATE_OFFLINE
            self._available = False

    def __del__(self):
        """Try to remove token."""
        if self._obtained_token is True:
            if self.access_token is not None:
                self.remove_token()

    @property
    def name(self):
        """Return the name of the cover."""
        return self._name

    @property
    def should_poll(self):
        """No polling needed for a demo cover."""
        return True

    @property
    def available(self):
        """Return True if entity is available."""
        return self._available

    @property
    def device_state_attributes(self):
        """Return the device state attributes."""
        data = {}

        if self.time_in_state is not None:
            data[ATTR_TIME_IN_STATE] = self.time_in_state

        if self.access_token is not None:
            data[CONF_ACCESS_TOKEN] = self.access_token

        return data

    @property
    def is_closed(self):
        """Return if the cover is closed."""
        if self._state is None:
            return None
        return self._state == STATE_CLOSED

    @property
    def device_class(self):
        """Return the class of this device, from component DEVICE_CLASSES."""
        return "door"

    def get_token(self):
        """Get new token for usage during this session."""
        args = {
            "grant_type": "password",
            "username": self._username,
            "password": self._password,
        }
        url = "{}/oauth/token".format(self.particle_url)
        ret = requests.post(url, auth=("particle", "particle"), data=args, timeout=10)

        try:
            return ret.json()["access_token"]
        except KeyError:
            _LOGGER.error("Unable to retrieve access token %s")

    def get_device_id(self):
        """Get device id from name."""
        url = "{}/api/v1/users/?access_token={}".format(
            self.particle_url, self.access_token
        )
        ret = requests.get(url, timeout=10)
        for automations in ret.json()["data"]["automations"]:
            if automations["info"]["name"] == self.device_name:
                _LOGGER.debug("UUID: %s" % automations["uuid"])
                _LOGGER.debug("Device Name: %s" % automations["info"]["name"])
                return automations["uuid"]

    def remove_token(self):
        """Remove authorization token from API."""
        url = "{}/v1/access_tokens/{}".format(self.particle_url, self.access_token)
        ret = requests.delete(url, auth=(self._username, self._password), timeout=10)
        return ret.text

    def _start_watcher(self, command):
        """Start watcher."""
        _LOGGER.debug("Starting Watcher for command: %s ", command)
        if self._unsub_listener_cover is None:
            self._unsub_listener_cover = track_utc_time_change(
                self.hass, self._check_state
            )

    def _check_state(self, now):
        """Check the state of the service during an operation."""
        self.schedule_update_ha_state(True)

    def close_cover(self, **kwargs):
        """Close the cover."""
        if self._state not in ["close"]:
            ret = self._put_command("close")
            self._start_watcher("close")
            return ret.get("status") == "done"

    def open_cover(self, **kwargs):
        """Open the cover."""
        if self._state not in ["open"]:
            ret = self._put_command("open")
            self._start_watcher("open")
            return ret.get("status") == "done"

    def stop_cover(self, **kwargs):
        """Stop the door where it is."""
        if self._state not in ["stopped"]:
            ret = self._put_command("stop")
            self._start_watcher("stop")
            return ret["status"] == "done"

    def update(self):
        """Get updated status from API."""
        try:
            status = self._get_variable("diagnosis")
            self._state = self._get_gate_status(status)
            _LOGGER.debug(self._state)
            self._available = True
        except requests.exceptions.ConnectionError as ex:
            _LOGGER.error("Unable to connect to server: %(reason)s", dict(reason=ex))
            self._state = STATE_OFFLINE
        except KeyError:
            _LOGGER.warning(
                "BFT device %(device)s seems to be offline", dict(device=self.device_id)
            )
            self._state = STATE_OFFLINE

        if self._state not in [STATE_MOVING]:
            if self._unsub_listener_cover is not None:
                self._unsub_listener_cover()
                self._unsub_listener_cover = None

    def _get_gate_status(self, status):
        """Get gate status from position and velocity."""
        _LOGGER.debug("Current Status: %s", status)
        first_engine_pos_int = status["first_engine_pos_int"]
        second_engine_pos_int = status["second_engine_pos_int"]
        first_engine_vel_int = status["first_engine_vel_int"]
        second_engine_vel_int = status["second_engine_vel_int"]

        if (
            (first_engine_pos_int == 100 and second_engine_pos_int == 100)
            and (first_engine_vel_int == 0 and second_engine_vel_int == 0)
        ):
            _LOGGER.warning("open")
            return STATES_MAP.get("open", None)
        if (
            (first_engine_vel_int == 0 and second_engine_vel_int == 0)
            and (first_engine_pos_int > 0 or second_engine_pos_int > 0)
        ):
            _LOGGER.warning("stopped")
            return STATES_MAP.get("stopped", None)
        if (
            (first_engine_pos_int == 0 and second_engine_pos_int == 0)
            and (first_engine_vel_int == 0 and second_engine_vel_int == 0)
        ):
            _LOGGER.warning("closed")
            return STATES_MAP.get("closed", None)
        if first_engine_vel_int > 0 or second_engine_vel_int > 0:
            _LOGGER.warning("moving")
            return STATES_MAP.get("moving", None)

    def _get_variable(self, var):
        """Get latest status."""
        api_call_headers = {"Authorization": "Bearer " + self.access_token}
        url = "{}/{}/execute/{}".format(self.dispatcher_api_url, self.device_id, var)
        _LOGGER.debug(url)
        ret = requests.get(url, timeout=10, headers=api_call_headers)
        return ret.json()

    def _put_command(self, func):
        """Send commands to API."""
        api_call_headers = {"Authorization": "Bearer " + self.access_token}
        url = "{}/{}/execute/{}".format(self.dispatcher_api_url, self.device_id, func)
        _LOGGER.debug(url)
        ret = requests.get(url, timeout=10, headers=api_call_headers)
        return ret.json()
