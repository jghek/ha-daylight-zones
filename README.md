# Night Compensation for Home Assistant

Drives lights per zone from how dark it is outside: on earlier in winter, a brighter boost at dusk
while your eyes are still used to daylight, a slow settle to a cosy level, a bedtime schedule,
sleep mode, and overrides for visitors. Uses the sun position, or a lux sensor if you have one.

**Status: 0.1.0, not released yet.** Implemented and tested, not yet run on a real Home Assistant.
The design, including how it works and every setting, is in [docs/design.md](docs/design.md).

## Install (once released)

1. HACS → ⋮ → Custom repositories → `https://github.com/jghek/ha-night-compensation`, type *Integration*.
2. Install **Night Compensation**, restart Home Assistant.
3. Settings → Devices & services → Add integration → **Night Compensation**.
4. On the integration page, **Add zone** for each group of lights.

## Development

```
python3 -m venv .venv && .venv/bin/pip install pytest-homeassistant-custom-component
.venv/bin/pytest
```
