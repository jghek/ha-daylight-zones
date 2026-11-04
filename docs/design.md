# Daylight Zones — design

Status: **implemented, not released** (2026-11-04). Version 0.1.0 is in this repository and tested, but not installed on any Home Assistant yet.

A Home Assistant custom integration that manages lights per **zone**: brightness and colour temperature that follow
the daylight outside, off hours, an auto switch and an override switch per zone. It replaces template sensors,
helpers and a script that runs every minute.

## 1. Goals and non-goals

Goals:

- Zones that may be a room, part of a room, or span several rooms. Each zone is configured on its own.
- One darkness axis per zone, the **effective elevation**, from the sun position.
- Brightness that follows daylight (on earlier in winter, a boost at dusk while eyes are still used to daylight, a slow
  settle to a cosy level), or a fixed level.
- Colour temperature that follows daylight: cool by day, warmer at dusk, warmest at night.
- Off hours: fade out at night and come back in the morning, or switch the zone off.
- Two switches per zone: **auto** (follow the sun and manage the lights) and **override** (lights at a set level).
- Respect people: a manual change to a light pauses that light.
- Every user-facing level is in percent (0–100 %). Angles are in degrees, colour temperatures in kelvin, durations in minutes or seconds.

Non-goals:

- Sleep mode, presence and motion. Sleep mode is one automation (blueprint in §9.3); presence and motion can drive a
  zone through the actions in §9.1.
- Colour (RGB). Only colour temperature.

## 2. Concepts

| Term | Meaning |
|---|---|
| Zone | A group of lights with one set of settings. One config subentry, one HA device. |
| Light | One light or switch in a zone, with its output settings (§8). One config subentry per light ("Add light"); a light belongs to at most one zone. |
| Effective elevation (E) | The darkness axis, in degrees: the real solar elevation shifted by season (§3). |
| Auto | Switch. On: the zone follows the sun and manages its lights. Off: lights off, and the zone leaves them alone. |
| Auto back on | Setting. When on, auto switches itself back on at the first solar noon or set time after it went off, however it went off. |
| Override | Switch. On: the zone's lights at the override level, also when auto is off. Ends when switched off, at the latest the next solar noon. |
| Brightness | `daylight` (curve over E, §4.1) or `fixed` (one level, §4.2). |
| Off hours | `none`, `window` (fade out at the off time, back at the on time) or `switch_off` (fade out at the off time, then auto off) (§6). |
| Zone level | The final level, 0–100 %. Exposed as `sensor.<zone>_level`. |
| Light output | Per light or switch: a dimmer (zone level mapped linearly onto min–scale) or on/off (on above a threshold) (§8). |
| Phase | Where the zone is: `day`, `evening_ramp`, `peak`, `settle`, `cosy`, `morning_ramp` (daylight brightness), `on` (fixed brightness), `off_fade`, `off_hours`, `on_fade` (off hours), `override`, `auto_off`. A change of phase ends manual pauses. |

## 3. Effective elevation

### 3.1 Sun model

The integration computes the real solar elevation `e` itself every minute (astral, HA's location).
The season term `s` runs from −1 at the June solstice to +1 at the December solstice, following the solar declination δ:

```
s = −δ / 23.44°
E = e − winter_bias × s
```

With `winter_bias = 3°` the effective sun is 3° lower in December (lights on earlier, colour warmer) and 3° higher in June.

### 3.2 Reachability caps

At 52°N the sun reaches only 14.7° on 21 December, and only gets down to about −14.5° in June. Each day the integration
computes the day's highest and lowest E; brightness thresholds above `highest − 1°` or below `lowest + 1°` are shifted
to that cap, keeping a ramp's width. `sensor.<zone>_level` shows `caps_active` while this happens.

## 4. Brightness

### 4.1 Daylight

Which curve applies depends on whether the sun is setting (after solar noon: evening) or rising (before solar noon: morning).
Both give 0 % in full daylight, so the switch at noon is continuous. Interpolation is linear in E.

Evening:

| Phase | E range | Level |
|---|---|---|
| `day` | E ≥ `evening_start` | 0 % |
| `evening_ramp` | `evening_start` > E ≥ `evening_full` | 0 → `peak` |
| `peak` | `evening_full` > E ≥ `hold_until` | `peak` |
| `settle` | `hold_until` > E ≥ `settle_end` | `peak` → `cosy` |
| `cosy` | E < `settle_end` | `cosy` |

Without boost, `peak = cosy` and the `peak` and `settle` phases disappear.

```mermaid
xychart-beta
    title "Evening curve, living room example (boost 100 %, cosy 60 %)"
    x-axis "Effective elevation (°)" ["20", "15", "12", "5", "0", "-6", "-9", "-12", "-15"]
    y-axis "Level (%)" 0 --> 100
    line [0, 0, 100, 100, 100, 100, 80, 60, 60]
```

Morning: no boost (eyes are adapted to the dark). `cosy` below `morning_start`, `cosy` → 0 % up to `morning_off`, then `day`.

Example timeline at 52°N, sun model, winter bias 0°, living room: evening 15° → 12°, hold until −6°, cosy by −12°.

| Date | Ramp 15°→12° | Sunset | Hold ends (−6°) | Cosy (−12°) |
|---|---|---|---|---|
| 21 June | 19:58 → 20:19 | 22:01 | 22:47 | 00:05 |
| 3 November | 14:52 → 15:21 | 17:05 | 17:38 | 18:18 |
| 21 December | capped to 13.7° → 10.7°: 13:27 → 14:25 | 16:27 | 17:06 | 17:49 |

### 4.2 Fixed

One level (`number.<zone>_level`) whenever auto is on; elevation has no effect on brightness. Phase `on`.
For zones like an office, where brightness should not change through the day but colour temperature should.

## 5. Colour temperature (optional)

Follows E with three points: `ct_night` at `ct_night_elevation` and below, `ct_dusk` at 0°, `ct_day` at `ct_day_elevation` and above, linear in between.

| E | Example |
|---|---|
| ≥ 20° | 5000 K |
| 10° | 4250 K |
| 0° | 3500 K |
| −4° | 2950 K |
| ≤ −8° | 2400 K |

- With daylight brightness, the boost at dusk is automatically cooler and the settle warmer: one story for brightness and colour.
- Only lights whose `supported_color_modes` include `color_temp` get a colour temperature, clamped to their own
  `min_color_temp_kelvin`/`max_color_temp_kelvin`. Other lights in the zone get brightness only.
- Colour temperature moves at most 100 K per minute; a command is sent when it differs 50 K or more from the last one sent.
- During an override the colour temperature keeps following daylight; only the brightness is overridden.

## 6. Off hours (optional per zone)

The off hours are the blocks of an HA **schedule helper** (Settings → Helpers → Schedule) chosen in the zone configuration,
so every day of the week has its own blocks, edited in HA's weekly editor. One schedule can serve several zones. `off_fade` (minutes) is a zone setting.

- A block cannot span midnight in the schedule helper, but a block ending at 24:00 and one starting at 00:00 the next day are one block for the zone:
  a night is drawn as, for example, Fri 23:00–24:00 plus Sat 00:00–07:00, and there is no new fade at midnight.
- The zone reads the blocks with `schedule.get_schedule` before its first update and again whenever the schedule changes or is edited,
  so a restart at night never switches lights on for a moment.
- The current setup, by the day after midnight, is Mon–Fri 00:00–07:00 and Sat–Sun 02:00–10:00.

| Mode | At the start of a block | Afterwards |
|---|---|---|
| `window` | fade to 0 over `off_fade` | 0 until the block ends, then fade back up over `off_fade`; the brightness curve keeps applying (level = curve × off hours) |
| `switch_off` | fade to 0 over `off_fade` | auto switches off. The zone stays off until it is switched on again, or comes back by its auto-back-on setting; the next switch-off is the first block start after that, so working late inside a block does not switch it off again the same night |

Outdoor example (`window`, curve 0° → −10°, a block 02:00–07:00, fade 30 min), at 52°N:

| Date | Evening on | Night | Morning |
|---|---|---|---|
| 4 November | 17:03 → full at 18:09 | 02:00 → off 02:30 | 07:00 fading on while daylight is already coming: about 40 % around 07:25, off at 07:42 |
| 21 December | 16:27 → full at 17:41 | 02:00 → off 02:30 | 07:00 → full at 07:30, fades out with daylight from 07:35 to 08:48 |
| 20 March | 18:50 → full at 19:53 | 02:00 → off 02:30 | stays off: sun up at 06:45 |
| 21 June | 22:00 → full at 23:40 | 02:00 → off 02:30 | stays off: sun up at 05:22 |

## 7. Composing the zone level

```mermaid
flowchart TD
    E["Effective elevation"] --> B{"Brightness"}
    B -- "daylight" --> C["Curve level"]
    B -- "fixed" --> F["Fixed level"]
    C --> M["× off hours"]
    F --> M
    M --> O{"Override on?"}
    O -- "yes" --> OV["Override level"]
    O -- "no" --> A{"Auto on?"}
    A -- "yes" --> Z["Zone level"]
    A -- "no" --> X["Lights off once, then left alone"]
    OV --> L["Per light: × max, min, colour temperature, manual pause"]
    Z --> L
    E --> K["Colour temperature"]
    K --> L
```

1. **Override** on: the override level, whatever auto is.
2. **Auto** off: when auto switches off the lights go off (the zone's `transition`, or the action's `transition`); after that the zone does not touch them,
   until auto is switched on again or the auto-back-on setting switches it on (first solar noon or set time after it went off).
3. Otherwise: brightness (daylight curve or fixed level) × off hours.

## 8. Driving the lights

- Per light or switch, one of two types:

  | Type | Settings | Output |
  |---|---|---|
  | `dimmer` (default for lights that can dim) | `scale` %, `min` % | 0 % zone level: off; 1–100 %: a straight line from `min` to `scale`, y = min + level × (scale − min) / 100. `scale` is what this light gets at 100 %, so lights of different strength can share a zone; `min` is where a dimmer starts to give light |
  | `on_off` (default for lights that cannot dim; always for `switch.*`) | `threshold` % | on when the zone level reaches the threshold, off when it drops more than 2 % below it, so it does not flicker around the threshold |

- Colour temperature is only sent to lights (not switches) that support it (§5).
- A command is only sent when on/off, the light's 0–255 brightness value, or its colour temperature (§5) changes. Measuring in brightness values
  keeps dim lights smooth: a light with a 4 % maximum has only about 10 values. Every command uses the zone's `transition` setting (default 1 s).
- **Rate limit**: the zone level moves at most `max_step` % per minute (default 5 %), so a changed setting moves the lights gradually. The limit applies to the zone level, so each light moves in proportion to its own range: a dimmer with scale 40 % moves at most 2 % of its full brightness per minute at the default.
  Switching auto or override bypasses the limit.
- **Manual change detection**: a change of a light's state, brightness or colour temperature whose context did not come from the integration
  pauses that light until the zone's next phase, or until auto or override switches. Not counted as manual: becoming unavailable and available again,
  a report within the transition time + 5 s after the integration's own command, or one that matches the command (3 %, 150 K).
- Recalculation every 60 s, and immediately on a change of a switch, a setting, or the off-hours schedule.

## 9. Actions, events and blueprints

### 9.1 Actions

All take `zone`: one or more zone devices.

| Action | Fields | Effect |
|---|---|---|
| `daylight_zones.auto_on` | | Auto on. |
| `daylight_zones.auto_off` | `transition` (s) | Auto off, lights off. Whether it comes back by itself is the zone's auto-back-on setting. |
| `daylight_zones.auto_toggle` | | Toggle auto. |
| `daylight_zones.override_on` | `level` (%), `until` (`next_phase` / `noon` / `duration`, default `noon`), `duration` | Override on. A `level` given here is for this override only; without it the zone's override level is used. |
| `daylight_zones.override_off` | | Override off. |
| `daylight_zones.override_toggle` | | Toggle override (until the next solar noon). |

### 9.2 Event

`daylight_zones_phase_changed` with `zone`, `zone_id`, `from`, `to`, for example "close the curtains when the living room enters `peak`".

### 9.3 Blueprints (in `blueprints/automation/daylight_zones/`)

- **Trigger to zone action**: any trigger (a button press type, a detached Shelly input, a door contact, an event) runs one of the actions in §9.1 on the chosen zones.
- **Sleep mode**: when a sleep-mode entity switches on, turn the chosen zones off with a fade. Zones with auto back on switched on come back by themselves.

## 10. Configuration

### 10.1 Zone configuration (config subentry)

Zone (subentry "Add zone", the cogwheel reconfigures it):

| Setting | Values |
|---|---|
| Name | text |
| Brightness | `daylight` / `fixed` |
| Dusk boost | yes/no (daylight only) |
| Colour temperature follows daylight | yes/no |
| Off hours | `none` / `window` / `switch_off`, and the schedule helper whose blocks are the off hours |

Light (subentry "Add light", one per light; the cogwheel reconfigures it, and moving a light to another zone is done there):

| Setting | Values |
|---|---|
| Light or switch | one `light.*` or `switch.*` entity, not in another zone |
| Zone | one of the zones |
| Type | `dimmer` (default for lights that can dim) / `on_off` (always for switches) |
| Scale %, Min % | dimmers (§8) |
| Threshold % | on/off (§8) |

The light's title is "<zone>: <friendly name>". Version 1 entries kept the lights inside the zone; they are migrated to light subentries on the first start of 0.2.0.
`sensor.<zone>_level` lists the zone's lights in its `lights` attribute, for example `dimmer 0-60 %`.

### 10.2 Zone entities

Entities for settings a zone does not use are not created.

| Entity | Unit | Used when | Example |
|---|---|---|---|
| `switch.<zone>_auto` | | always | on; attribute `until`: when it comes back on by itself |
| `switch.<zone>_auto_back_on` | | always | off |
| `select.<zone>_auto_back_on_at` | `noon` / `time` | always | `noon` (solar noon) |
| `time.<zone>_auto_back_on_time` | time | always | 12:00 |
| `switch.<zone>_override` | | always | off; attribute `until` |
| `sensor.<zone>_level` | % | always | attributes: phase, curve level, off-hours level, effective elevation, caps, auto, override, paused lights |
| `sensor.<zone>_phase` | enum | always | `cosy` |
| `sensor.<zone>_effective_elevation` (diagnostic) | ° | always | −13.2 |
| `sensor.<zone>_colour_temperature` | K | colour temperature | 2400 |
| `number.<zone>_evening_ramp_start` / `_evening_ramp_full` | ° | daylight | 15 / 12 |
| `number.<zone>_boost_peak` / `_boost_hold_until` / `_settle_to_cosy_by` | % / ° / ° | daylight + boost | 100 / −6 / −12 |
| `number.<zone>_cosy_level` | % | daylight | 60 |
| `number.<zone>_morning_ramp_start` / `_morning_off` | ° | daylight | 12 / 15 |
| `number.<zone>_level` | % | fixed | 80 |
| `number.<zone>_winter_bias` | ° | daylight or colour temperature | 3 |
| `number.<zone>_maximum_step` | %/min | always | 5 |
| `number.<zone>_transition` | s | always | 1 (Shelly Gen1 lights cap transitions at 5 s) |
| `number.<zone>_colour_temperature_day` / `_dusk` / `_night` | K | colour temperature | 5000 / 3500 / 2400 |
| `number.<zone>_day_colour_from` / `_night_colour_from` | ° | colour temperature | 20 / −8 |
| `number.<zone>_off_hours_fade` | min | off hours | 30 |
| `number.<zone>_override_level` | % | always | 80 |

Settings, the auto switch and the override switch survive restarts. The moment auto comes back on is computed from when it went off and the current auto-back-on setting, so a setting changed while the zone is off applies at once. An end that passed while Home Assistant was down takes effect on the first update after the restart.

## 11. Implementation outline

- Domain `daylight_zones`, `integration_type: service`, `iot_class: calculated`, single config entry, one subentry and one device per zone.
  No requirements beyond astral, which HA ships.
- `curve.py`: pure functions (effective elevation, caps, curves, colour temperature, off hours, composition), no HA imports.
- `zone.py`: one controller per zone: timer, listeners, switches, rate limits, manual-change detection, sending commands.
- Platforms: `sensor`, `number`, `time`, `switch`.
- Tests:
  - `curve.py` against the timelines in §4.1 and §6, the caps in §3.2, colour temperature, and off hours including working late;
  - a comparison with the replaced template formulas over a full year (sun factor) and a week (off hours);
  - controller tests with fake lights: auto and override, fixed brightness, colour temperature and clamping, manual pauses (brightness and colour), switch-off, rate limit, restarts;
  - an end-to-end run of 26 hours minute by minute against the replaced script: at most 1 brightness value apart, 20 commands instead of 1 560;
  - both blueprints loaded through HA's blueprint and automation validation.

## 12. Example zones and migration (later, not now)

The migration ports today's behaviour 1:1 first (boost off, cosy 100 %, maximum step 100 %/min, winter bias 0°, no colour temperature),
so nobody at home notices the switch. New features are enabled afterwards. It is done while someone who knows about it is at home.

| Zone | Lights (dimmer scale %) | Brightness | Off hours | Colour temperature |
|---|---|---|---|---|
| Outside | `terrasdeur_light`, `voordeur_light` (6); `shellydimmer2_48e7297d2798` (oprit, 100); `shellyprodm1pm_fce8c0d81a74` (tuin achter, 15); `kantoor_buiten_achter`, `kantoor_buiten_voor` (8); `shellyprodm1pm_a0dd6c9e2390_light_0` (tuin voor, 15) | daylight 0° → −10°, morning −10° → 0° | window, schedule Mon–Fri 00:00–07:00, Sat–Sun 02:00–10:00 | no |
| Hallway | `gang_achter`, `gang_trap`, `gang_voordeur`, `gang_woonkamer` (4) | daylight 10° → 3°, morning 3° → 10° | window, the same schedule | later |
| Living room mood | `woonkamer_sfeer_no`, `_nw`, `_zo`, `_zw`, `_tv` (100) | daylight 10° → 3°, morning 3° → 10° | none | later |
| Living room spots | `woonkamer_boekenkast` (90), `woonkamer_spots_wand` (100) | daylight 10° → 3°, morning 3° → 10° | none | later |
| Office (new) | `kantoor_voor`, `kantoor_achter` (WW/CW template lights) | fixed 80 % | switch_off, a schedule with a 22:00–24:00 block each day | yes |

- Sleep mode: the sleep-mode blueprint with `input_boolean.nightmode` turns Hallway and both Living room zones off with a 10 s fade; those zones have auto back on at solar noon. The office keeps auto back on off.
- The office is switched by a trigger-to-zone-action automation (detached Shelly input or door contact → toggle the office zone).
- Removed afterwards:
  - template sensors `indoor_nightcompensation_sun`, `outdoor_nightcompensation_sun`, `nightcompensation_time`, `indoor_nightcompensation`, `outdoor_nightcompensation`;
  - script `nachtverlichting`, automation `Nachtverlichting`, and the `script.nachtverlichting` calls in the two sleep-mode button automations;
  - helpers: the four `*_solar_elevation_on/off`, `night_time_dim_minutes`, the four `lights_on/off_*`, the nine brightness helpers, and
    `gang_verlichting_handmatig` (replaced by `switch.hallway_override` with override level 39 %, as it sets brightness 100 of 255).

Known issues in the current setup that this fixes:

- An off time before midnight turns the lights back on after the fade.
- `woonkamer_boekenkast` is set twice per minute, by the mood block and by its own block.
- Commands are sent every minute, which undoes manual dimming and adds Wi-Fi traffic to Shellys.
- An indoor off threshold of 15° is never reached around December; the lights stay dimmed on all day.
- Equal on and off elevations divide by zero.

## 13. Decided

- Q-001 (2026-11-04): no lux sensor for now; the zones use the sun model only. A lux source (a sky-facing sensor converted to an
  equivalent elevation) can be added later; the earlier version is in the git history.
- Q-002 (2026-11-04): a zone does not skip a short morning switch-on (§6, the 07:00–07:42 case in November): the light helps when leaving early.
- Q-003 (2026-11-04): `max_step` is a zone setting (`number.<zone>_maximum_step`, 1–100 %/min), default 5 %/min. It is enough for the fastest
  evening ramp (21 June, 0 → 100 % in 21 min, about 4.8 %/min) and scales per light with its range (§8).
