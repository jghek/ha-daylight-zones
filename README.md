# Daylight Zones for Home Assistant

Manages lights per zone (a room, part of one, or several rooms): brightness and colour temperature follow the daylight outside,
off hours fade lights out at night, and every zone has an **auto** switch (follow the sun) and an **override** switch (a set level, for visitors).

- Brightness follows daylight (on earlier in winter, a brighter boost at dusk, a slow settle to a cosy level), or stays at a fixed level.
- Colour temperature follows daylight: cool by day, warmer at dusk, warmest at night. Only for lights that support it.
- Off hours: fade out at an off time and come back at an on time, or fade out and switch the zone off.
- A light you change by hand is left alone until the zone's next phase.
- Blueprints: run a zone action from any trigger (button, detached switch input, door contact), and turn zones off with sleep mode.

Do not let Adaptive Lighting control the same lights.

**Status: 0.1.0, not released yet.** Implemented and tested, not yet run on a real Home Assistant.
How it works and every setting: [docs/design.md](docs/design.md).

## Install (once released)

1. HACS → ⋮ → Custom repositories → `https://github.com/jghek/ha-daylight-zones`, type *Integration*.
2. Install **Daylight Zones**, restart Home Assistant.
3. Settings → Devices & services → Add integration → **Daylight Zones**.
4. On the integration page, **Add zone** for each group of lights.
5. Optional: import the blueprints from `blueprints/automation/daylight_zones/`.

## Development

```
python3 -m venv .venv && .venv/bin/pip install pytest-homeassistant-custom-component
.venv/bin/pytest
```
