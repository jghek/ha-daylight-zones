"""End to end: a zone set up like the template/script setup it replaces sends the same brightness, minute by minute."""

from datetime import timedelta

from astral import Observer
from astral.sun import elevation
from homeassistant.core import ServiceCall
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from .conftest import setup_zone
from .test_curve import old_sun_factor, old_time_factor

PORT = {"lights": {"light.a": {"type": "dimmer", "scale": 4, "min": 0}}, "brightness": "daylight", "use_boost": False, "use_color_temp": False,
        "off_hours": "window", "off_schedule": "schedule.nights"}
SETTINGS = {"evening_ramp_start": 15, "evening_ramp_full": 12, "cosy_level": 100, "morning_ramp_start": 12, "morning_off": 15, "maximum_step": 100}


async def test_port_matches_template_formula(home, freezer):
    sent = {"brightness": 0, "calls": 0}

    async def record(call: ServiceCall) -> None:
        sent["brightness"] = call.data.get("brightness", 0) if call.service == "turn_on" else 0
        sent["calls"] += 1

    home.services.async_register("light", "turn_on", record)
    home.services.async_register("light", "turn_off", record)
    freezer.move_to("2026-11-06 12:00:00+01:00")  # Friday noon: weekday evening, weekend night, Saturday morning
    await setup_zone(home, PORT, title="Port")
    for key, value in SETTINGS.items():
        await home.services.async_call("number", "set_value", {"entity_id": f"number.port_{key}", "value": value}, blocking=True)

    observer = Observer(52.0, 5.0)
    worst, minutes, lit = 0, 26 * 60, 0
    for _ in range(minutes):
        freezer.tick(timedelta(minutes=1))
        async_fire_time_changed(home, dt_util.utcnow())
        await home.async_block_till_done()
        now = dt_util.now()
        expected = int(old_time_factor(now) * old_sun_factor(elevation(observer, now), 12, 15) * 0.04 * 255)
        worst = max(worst, abs(sent["brightness"] - expected))
        lit += expected > 0
    assert lit > 8 * 60  # the comparison covers a real evening and night, not only daylight
    assert sent["calls"] < minutes / 4  # only changes are sent, not a command every minute
    assert worst <= 1, worst  # round() vs int()
