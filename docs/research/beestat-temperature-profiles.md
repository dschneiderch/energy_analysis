# beestat temperature profiles: how they are made

Research for the insulation-impact sub-project, 2026-11-07.

beestat is a website that reads ecobee thermostat data and draws "temperature profiles". Its code is
public ([beestat/app](https://github.com/beestat/app), GPL-3.0 license). This note is based on two
of its files: `api/profile.php` (builds the profiles, last changed 2026-10-23) and `api/runtime.php`
(stores the ecobee data).

The goal: compute profiles from our own ecobee data that can be compared with beestat's.

## What a profile tells you

A profile answers one question for each thermostat: **how fast does the indoor temperature change,
at each outdoor temperature?**

- The answer is in degrees per hour (°F/h). Positive means the house is warming, negative means it is
  cooling.
- It is worked out for each whole degree of outdoor temperature (for example 30 °F, 31 °F, 32 °F).
  Later sections call each of these one-degree slots a **bin**.
- Each value is the middle value (median) of all the measured stretches at that outdoor temperature.

beestat makes a separate set of numbers for each thing the system can be doing:

| What was running | beestat's name |
| --- | --- |
| Normal heating (heat pump or furnace), backup heat off | `heat_1` (and `heat_2` for a second, stronger stage) |
| Backup heat (for example electric heat strips) | `auxiliary_heat_1`, `auxiliary_heat_2` |
| Cooling | `cool_1`, `cool_2` |
| **Everything off** in every thermostat in the house | `resist` |

The two most useful ones for us:

- **Heating on:** how fast the house warms while heating.
- **Everything off:** how fast the house cools on its own, drifting toward the outdoor temperature.
  This is the house's heat loss. A slower drift means less heat loss.

### The straight-line summary

beestat also draws a straight line through the points (one point per outdoor temperature). The line
is described by two numbers:

- **Slope:** how much the rate changes per degree of outdoor temperature.
- **Intercept:** where the line sits (its value at 0 °F outdoors).

### The balance point

The balance point is the outdoor temperature where the line crosses zero (−intercept ÷ slope).
beestat reports it only when the slope is positive, and for heating only on heat-pump thermostats.

- For **everything off:** the outdoor temperature at which the house neither gains nor loses heat.
- For **heating on:** the outdoor temperature at which heating can no longer raise the indoor
  temperature.

## When beestat makes profiles

- beestat recomputes each thermostat's profile when it syncs with ecobee, and keeps **one profile per
  week**.
- Each profile covers the **past 365 days** before it was made.
  - A per-thermostat setting can shorten this. It is not set on this account.
  - Early profiles had less data, so they cover less time (for example 177 days).
- A recompute in the same week **overwrites** that week's profile. It keeps the same ID and only
  changes the date.
- beestat's API (its interface for other programs) lets you read your own saved profiles with your
  API key (`profile.read_id`). This was added in 2023-12 ("Exposed profiles in the API") but is not in
  beestat's published API docs.
- We save a copy of every profile: files in `data_archive/beestat/profile_*.json`, and the
  `beestat_profile` table in our database.

## How beestat reads the thermostat data

Before computing anything, beestat takes ecobee's 5-minute readings and prepares them. Each 5-minute
reading has, among other things:

- the indoor temperature (an average over the sensors the current comfort setting uses, so it can
  switch rooms when the comfort setting changes)
- the outdoor temperature
- how many seconds in those 5 minutes the heat pump (the "compressor") ran, and whether it was
  heating or cooling
- how many seconds the backup heat (ecobee calls it "aux", short for auxiliary) ran
- the thermostat mode (heat, cool, auto, off), the comfort setting (such as Home or Sleep), any
  scheduled event, and the target temperatures

### Readings beestat throws away

- The **whole 5-minute reading** is thrown away if the outdoor temperature is below −1000 (an error
  value ecobee sometimes sends), or if any of these is missing:
  - the thermostat mode
  - the indoor temperature
  - the indoor humidity
  - any of the heating, cooling or backup-heat run times
  - the run times of the fan or other add-on equipment (humidifier, dehumidifier, ventilator,
    economizer)
- It is also thrown away if the second stage of backup heat ran longer than the first, which should
  not be possible.

We do not download humidity or the add-on equipment readings. Instead, we throw away readings with a
missing thermostat mode, indoor temperature or run time. According to a comment in beestat's own
code, missing humidity and add-on readings are rare.

### How beestat decides whether heating was on (called "heat normalization" in other notes)

For each 5-minute reading, beestat has to decide: was the heating on? Its rule depends on what kind
of equipment the thermostat is set up for.

| Thermostat set up for | What beestat counts as "heating" |
| --- | --- |
| A heat pump (beestat: `compressor` or `geothermal`) | Heat-pump run time while it was heating. Backup heat stays separate. |
| Anything else (furnace, electric heater) | The backup-heat ("aux") run time. Backup heat is then set to zero. |

**This house has one heat pump that serves two thermostats:**

- **Living Room** is set up as a heat-pump thermostat (beestat: `compressor`). Its heating shows up
  as heat-pump run time.
- **Upstairs** is set up as if it had an ordinary electric heater (beestat: `electric`). When it
  calls for heat, the same heat pump runs, but ecobee records that time as "aux".

Because of the second rule, Upstairs still gets a usable heating-on profile: its "aux" time is
counted as heating.

### The Upstairs problem: a worked example

To find "everything off" stretches, beestat needs to know that **both thermostats in the house** had
the heating off. So when it builds the profile for one thermostat, it also looks at the other one.

The problem: it reads **both** thermostats' data with the rule of the thermostat it is building the
profile for. When it builds the Upstairs profile, it reads the Living Room's data with the Upstairs
rule ("heating = aux time"). But the Living Room's heating is heat-pump time, not aux time. So
beestat thinks the Living Room was off even while the heat pump was heating it.

Three example 5-minute readings (run times in seconds out of 300):

| Reading | Living Room heat pump | Living Room aux | Upstairs heat pump | Upstairs aux | What really happened |
| --- | --- | --- | --- | --- | --- |
| A | 300 | 0 | 0 | 0 | Heat pump heating the Living Room |
| B | 0 | 0 | 0 | 0 | Everything off |
| C | 0 | 0 | 0 | 300 | Heat pump heating Upstairs |

**beestat building the Upstairs profile** (both thermostats read with the Upstairs rule, "heating =
aux time"):

| Reading | Living Room | Upstairs | beestat's conclusion |
| --- | --- | --- | --- |
| A | aux 0 → off | aux 0 → off | Whole house off → counted as a heating-off reading (**wrong**) |
| B | off | off | Whole house off → heating-off reading (right) |
| C | off | aux 300 → on | Upstairs heating (right) |

**Corrected: each thermostat read with its own rule:**

| Reading | Living Room (heat-pump rule) | Upstairs (aux rule) | Conclusion |
| --- | --- | --- | --- |
| A | heat pump 300 → on | off | Living Room on → **not** a heating-off reading |
| B | off | off | Whole house off → heating-off reading |
| C | off | on | Upstairs heating |

Only reading A changes. Any 5 minutes in which the heat pump heats only the Living Room can be
wrongly counted as Upstairs "everything off" data.

**Why the Living Room profile is not affected:** when beestat builds the Living Room profile, it reads
both thermostats with the heat-pump rule. That rule keeps aux time as "something running". So in
reading C, Upstairs aux 300 counts as on, and the house is correctly not "all off".

### How much it changes the Upstairs numbers

We computed the Upstairs profile both ways (2026-11-07), using only outdoor temperatures of 55 °F
and below. "Difference" is beestat's value minus the corrected value.

| Upstairs | beestat's way | Each thermostat by its own rule | Difference |
| --- | --- | --- | --- |
| Everything off (resist), Apr 16 – Nov 23 2025 | −0.56 °F/h, 3,079 samples | −0.53, 2,758 | −0.03 |
| Everything off (resist), Dec 2 2025 – Nov 7 2026 | −0.72 °F/h, 4,663 samples | −0.58, 3,496 | −0.14 |
| Heating on (heat_1), Dec 2 2025 – Nov 7 2026 | +2.61 °F/h, 220 samples | +2.47, 275 | +0.14 |

What this means:

- **Extra heating-off samples.** About 10–25 % of beestat's heating-off samples are such extra
  times, when the heat pump heats the Living Room. With them, Upstairs seems to lose heat *faster*. That is probably
  because those are the colder stretches (not checked).
- **The error is not constant.** It grows with how much the Living Room heats, so it differs between
  periods: −0.03 in a mostly warm period, −0.14 in one that includes winter. That is larger than the
  before/after insulation changes we are trying to measure.
- **beestat's archived Upstairs heating-off results can't be used for a before/after comparison.**
  They also can't be corrected, because beestat no longer has the 5-minute data.
- **Upstairs heating-on results are usable**, with an error of about 0.1 °F/h. They include times
  when both thermostats call for heat at once. The corrected way finds *more* heating samples (275 vs
  220), probably because the Living Room's heating now interrupts Upstairs stretches and splits them
  into shorter ones (not checked).
- **The Living Room profile is not affected** (see above).
- **A second, separate problem for Upstairs:** the indoor temperature the Upstairs thermostat reports
  comes from a different room depending on the comfort setting: the Kids' Room in Sleep and Weekend
  Home, the Office in Home. The thermostat's own sensor mostly follows the Living Room zone, because
  of where it sits. See `house-profile-options.md` for a profile that uses a fixed average of the
  Upstairs rooms instead.

### Reference: ecobee readings and where they are in our database

Use this table to find the data beestat uses in our own database (table `ecobee_interval`).

| beestat field | from ecobee | our `ecobee_interval` column |
| --- | --- | --- |
| `indoor_temperature` | `zoneAveTemp` | `indoor_temp_f` |
| `outdoor_temperature` | `outdoorTemp` (whole reading dropped if < −1000) | `outdoor_temp_f` |
| `compressor_mode` + `compressor_1` | `compCool1` > 0 → cool; else `compHeat1` > 0 → heat; else off | `comp_cool_s`, `comp_heat_s` |
| `auxiliary_heat_1` | `auxHeat1` | `aux_heat_s` |
| `system_mode` | `HVACmode` | `hvac_mode` |
| `event_runtime_thermostat_text_id` | `zoneCalendarEvent` | `calendar_event` |
| `climate_runtime_thermostat_text_id` | `zoneClimate` | `climate` |
| `setpoint_heat`, `setpoint_cool` | `zoneHeatTemp`, `zoneCoolTemp` | `heat_setpoint_f`, `cool_setpoint_f` |

ecobee columns behind the "thrown away" rules: `HVACmode`, `zoneAveTemp`, `zoneHumidity`,
`compHeat1/2`, `compCool1/2`, `auxHeat1/2`, `fan`, `humidifier`, `dehumidifier`, `ventilator`,
`economizer`, the check `auxHeat2 > auxHeat1`, and `outdoorTemp < -1000`.

### Where to find this in the code

- `src/energy/calc/profile.py` copies beestat's handling, including the Upstairs problem, so its
  results stay comparable with beestat's.
- `src/energy/calc/house_profile.py` reads each thermostat with its own rule.

## How beestat turns the readings into a profile, step by step

(In the code: `profile::generate` in `api/profile.php`.)

This happens in two stages:

- **From readings to measured stretches.** beestat walks through the year of data a profile covers,
  one 5-minute reading at a time. It looks for stretches of time when one thing was going on, for
  example everything off, and turns each stretch into one rate in °F per hour.
- **From measured stretches to the profile.** It sorts these rates into bins, takes the middle value
  of each bin, and draws the straight line.

Some words used below:

- A **sample** is one measured stretch of time, for example 40 minutes with everything off, turned
  into a °F-per-hour rate. (The tables in the first half of this note count these.)
- **Both thermostats** means every active thermostat at the same address. Here that is Living Room
  and Upstairs. (beestat calls this the "group".)
- Each 5-minute reading describes the state at the **end** of its 5 minutes. A reading stamped 21:05
  tells you what was going on from 21:00 to 21:05, and the indoor temperature at 21:05.

### Stage 1: from 5-minute readings to measured stretches

**Step 1. Check both thermostats at each reading.** At each 5-minute step, beestat asks two
questions:

- **Is the whole house off?** Yes only if both thermostats have a reading for this time, neither
  shows any heating, cooling or backup-heat run time, and neither is missing its indoor or outdoor
  temperature. (In the code: `all_off`.)
- **Is everything else off?** The same question, but ignoring the thermostat the profile is for. This
  is used for heating-on and cooling stretches: the profiled thermostat may run, the other one must
  be off. (In the code: `most_off`.)

If a thermostat that should have data has no reading for that time, both answers are "no". A
thermostat whose history hasn't started yet at that time is ignored (in the code: its `data_begin`
is later).

Remember the Upstairs problem from the first half: "off" here is judged with the profiled
thermostat's rule for both thermostats.

**Step 2. When a stretch starts.** beestat notes the reading at which a stretch begins (in the code:
`begin_runtime[type]`):

| Kind of stretch | It starts when |
| --- | --- |
| Everything off (`resist`) | the first reading that shows the whole house off (that reading covers the 5 minutes before it) |
| Heating on (`heat_1`) | the profiled thermostat has been heating for at least 5 minutes, everything else is off, and no backup heat is running |
| Backup heat (`auxiliary_heat_1`) | backup heat has been on for at least 5 minutes and everything else is off. The heat pump may run at the same time. |
| Cooling (`cool_1`) | cooling has been on for at least 5 minutes, everything else is off, and the second cooling stage is off |

**Step 3. When a stretch ends.** A running stretch ends at the first reading where any of these
happens:

- The outdoor temperature, rounded to a whole degree, is different from the one at the start. (So
  every stretch stays in one bin.)
- The scheduled event or the comfort setting is different from the one at the start.
- For heating or cooling: the other thermostat turns on. For everything off: anything turns on.

**Step 4. Which reading counts as the end.** Normally the stretch's last reading is the one just
before the reading that ended it (5 minutes earlier).

- **If the event or comfort setting changed**, the end moves back to **30 minutes before the
  change**. The reason: the indoor temperature is an average over the sensors the comfort setting
  uses (see "How beestat reads the thermostat data"). When the setting changes, the set of rooms
  changes, and the average jumps.
- **Otherwise**, beestat looks up to 30 minutes ahead. If an event or comfort-setting change is
  coming, the end again moves to 30 minutes before that change.
- If there is no reading at the end point, or the end is not later than the start, the stretch is
  thrown away. beestat does not look for another end point.

**Step 5. Minimum length.** A stretch must last at least:

- **5 minutes** for heating, backup heat and cooling
- **30 minutes** for everything off

Shorter stretches are thrown away. A comment in beestat's code notes that a longer minimum for
everything off tilts the results toward calm, sunny periods.

**Step 6. Turn the stretch into a rate.** The rate is the indoor temperature change divided by how
long the stretch lasted, in °F per hour:

rate = (indoor temperature at the end − indoor temperature at the start) ÷ length in seconds × 3600

The rate goes into the bin of the outdoor temperature at the **start** of the stretch, rounded to a
whole degree. (In the code: `delta_per_hour`.)

**Step 7. Start the next stretch, or forget it.** When a stretch ends, a new stretch of the same kind
starts right away at the current reading, if that reading still meets the start rules. beestat
forgets a started stretch, without recording anything, when the heating, cooling or everything-off
state stops, when a temperature is missing, or, for heating on, when backup heat turns on.

So a heating or cooling stretch is recorded only if something from step 3 ends it first: an
outdoor-temperature change, a comfort-setting change, or the other thermostat turning on. The last
part of a heating run that simply stops because the house is warm enough is not recorded.
Everything-off stretches are different: anything turning on is one of their end reasons, so they
are recorded.

**Step 8 (setting, not used here). Ignore daytime.** A thermostat setting can make beestat skip all
readings between sunrise and sunset, to leave out sun heating. It is off on this account. (In the
code: `ignore_solar_gain`.)

#### Example: one everything-off stretch

This example is made up. It only follows the rules above.

| Time | What happens | Indoor | Outdoor |
| --- | --- | --- | --- |
| 21:00 | The heating stops. | | |
| 21:05 | First reading with the whole house off, so the stretch starts (step 2). It goes into the 35 °F bin (35.4 rounded). | 68.0 °F | 35.4 °F |
| 21:10 – 21:50 | Still everything off, outdoor still rounds to 35 °F. | | |
| 21:55 | | 67.4 °F | 35.3 °F |
| 22:00 | Outdoor now rounds to 36 °F, so the stretch ends (step 3). | | 35.6 °F |

- **Which end point:** the reading before 22:00, so 21:55 (step 4). No comfort-setting change is
  coming in the next 30 minutes, so the end stays there.
- **Long enough?** 21:05 to 21:55 is 50 minutes, more than the 30-minute minimum (step 5).
- **Rate:** (67.4 − 68.0) ÷ 3,000 seconds × 3600 = −0.72 °F/h, filed in the 35 °F bin (step 6).
- **Next:** at 22:00 everything is still off, so a new stretch starts right away, now in the 36 °F
  bin (step 7).

If the Sleep setting had started at 22:00 instead, the end would have moved back to 21:30. The
stretch (21:05 to 21:30, 25 minutes) would then have been shorter than 30 minutes and thrown away.

### Stage 2: from measured stretches to the profile

**Step 9. One value per bin.** For each one-degree outdoor bin, beestat takes the middle value
(median) of all the rates in it, rounded to 0.01 °F/h. A bin is kept only if it has **at least 2
samples**.

**Step 10. Enough bins.** A kind of stretch (heating on, everything off, and so on) gets a profile
only if it has **at least 5 bins**.

**Step 11. Drop bins that are far off the line.** beestat draws a first best-fit straight line
through the bins. Every bin counts the same, however many samples it has. (This is the usual
"least squares" fit, unweighted.) Then:

1. It measures how far each bin is from that line.
2. It works out the average of those distances and their spread. (The spread is the "standard
   deviation": a measure of how much the distances differ from their average.)
3. It drops every bin whose distance is outside the average ± **2 standard deviations**.
4. It draws the final line (the slope and intercept in the profile) through the bins that are left.

One odd effect: if the average minus 2 standard deviations is above zero, bins that are unusually
*close* to the line are dropped as well.

## The other numbers in a profile

Besides the bins and the line, a profile has a few more numbers. We don't use them, and some of them
are misleading for this house.

- **Average target temperatures** (`setpoint.heat`, `setpoint.cool`): the average target
  temperature over all readings in heat or auto mode (for cooling: cool or auto mode). For heating,
  the code checks whether the *cooling* target is missing instead of the heating target. That looks
  like a copy-and-paste mistake.
- **Degree days** (`degree_days`): for each calendar day (in the beestat server's time zone), the
  average of all outdoor temperatures read that day from both thermostats, minus 65 °F. These are
  added up into a heating total and a cooling total.
- **Run time** (`runtime`, `runtime_per_degree_day`): minutes of run time added up over **every
  reading of both thermostats**, using the profiled thermostat's rule for both. beestat reads the
  data in pieces that overlap by 30 minutes, and the readings in those overlaps are counted twice.
  - So these numbers are **not per thermostat**.
  - For the Living Room, Upstairs heating ends up under backup heat (`auxiliary_heat_1`), because
    Upstairs records its heating as aux.
  - Don't use these numbers to compare the two thermostats.

## Our own code and how well it matches beestat

`src/energy/calc/profile.py` copies the temperature part of beestat's method: the rates, the
straight lines and the balance points.

- It leaves out the comfort setting temperatures, degree days, run time, setback, and second-stage
  equipment (these thermostats don't have any).
- You give it a start and an end. These can be dates (whole local days, both included) or exact
  times. (In the code: `generate(conn, ecobee_id, start, end)`.)
- To cover the same year as a beestat profile, use start = the profile's creation time minus 1
  calendar year, and end = the creation time (`generated_at`).

**Check against beestat (2026-11-07).** We compared our results with every saved beestat profile
whose year of data lies inside our 5-minute data. Those are the profiles made 2026-04-23 to
2026-10-31, for both thermostats: 12 profiles.

- **Profiles from July–October 2026:**
  - Heating on and cooling: every bin identical.
  - Everything off: at most 2 of about 70 bins differ, by 0.01 °F/h (probably rounding of values
    that end in exactly half).
  - The straight lines agree to the 4th decimal. Balance points agree within 0.1 °F.
- **Profiles from April–May 2026:** their year of data starts right after our data starts
  (2025-04-16).
  - Up to 10 everything-off bins and 2 heating bins differ. The average difference is at most
    0.025 °F/h.
  - The Living Room heating-on balance point moves by up to 3 °F. But that line is nearly flat, so
    its balance point means little anyway.
- Each profile takes about 2 seconds to compute.

## Numbers for the insulation analysis

- **Everything-off slope** (°F/h per °F outdoor). With the heating off, the house cools at a rate
  roughly proportional to how much warmer it is than outside (indoor drift ≈ −k × (indoor −
  outdoor)). The slope estimates k: how fast the house loses heat, divided by how much heat it
  stores. **Lower means slower heat loss.** The everything-off value at one fixed outdoor
  temperature (for example 40 °F) shows the same thing, in °F/h.
- **Height of the heating-on line** at a fixed outdoor temperature (°F/h): how fast the house warms
  while heating. The heating *slope* says how much that rate drops as it gets colder. Two things
  cause the drop: the house loses more heat, and the heat pump puts out less. Better insulation
  should raise the cold end of the line.
- **Heating balance point** (where the heating-on line crosses zero, −intercept ÷ slope): the outdoor
  temperature at which heating can no longer raise the indoor temperature. Trust it only when it is
  close to the outdoor temperatures that actually have bins. A nearly flat line puts it far away (Living
  Room, 2026-10-31: −343 °F).

**Compare the cold bins, not the slopes of the whole lines.** Whole-line slopes are misleading:

- The lines run from about 20 °F to 95 °F outdoors. So summer bins, where the sun heats the house,
  drive the everything-off slope.
- The heating slope swings with whatever cold spells happen to be in the year of data. The Living
  Room heating-on slope jumped from 0.003 to 0.034 when the 2025-02 cold spell (−3 °F) entered the
  past-365-days period, and fell again when it dropped out in 2026-02.
- Comparing whole-line slopes once produced a false 16–23 % "improvement"
  (`docs/summaries/insulation-effect.md`, "Withdrawn result").

### How to compare before and after (the insulation check)

- **Compare bin by bin.** Take the bins both periods have, at or below 55 °F outdoors, and average
  the change in °F/h over them.
- **The 55 °F upper cutoff is on purpose:** above it, heating by the sun dominates.
- **There is no lower cutoff.** The data itself stops near 21 °F, because an everything-off stretch
  needs both thermostats off for 30 minutes, which is rare in the cold. A mild winter adds no cold
  bins at all.
- **Compare against natural variation.** Put the before/after change next to the difference between
  two profiles that were both made before the work. That shows how much the numbers move on their
  own.
  - The profile pair 2024-11-19 → 2025-11-18 doesn't overlap. For the Living Room, the 2024-11-19
    profile's data starts after the 2024-03 history reset, so it has no aux period. For Upstairs it
    covers 2023-11 → 2024-11, including the Living Room aux winter (see the next section).
  - The pair 2025-03-01 → 2025-11-18 is clean, but the two years share the whole 2024–25 winter, so
    it understates the variation.
- **Use the same season on both sides.** Winter and spring/fall stretches differ at the same outdoor
  temperature, by about as much as the effect being measured.

Results, and what they mean for the insulation work, are in `docs/summaries/insulation-effect.md`.

## History of the archived profiles (which ones are usable)

This comes from the 288 saved profiles and beestat's daily run times.

- **Living Room aux period: November 2023 – March 2024.**
  - In that period, the heat pump's running was recorded as aux: 18–234 hours a month. After that
    it was near zero. (The backup heat strips have essentially never run.)
  - Profiles from that winter (2023-12 to 2024-02) have a backup-heat curve (`auxiliary_heat_1`).
    Their heating-on curve only sees the part of the heat pump's running that was recorded as
    heating. **Don't use them.**
- **History resets.** beestat's 5-minute history started over around 2024-03-05 for the Living Room
  (the profile's length of data drops to 0) and around 2024-03-21 for Upstairs (21 days of data).
  That is right when the aux period ended, probably because the thermostats were reprogrammed.
  Profiles until about 2025-03 cover less than a year (`metadata.duration` < 365).
- **Clean full-year profiles from before the insulation work: made 2025-03-01 to 2025-11-18.**
  - Each covers a full 365 days.
  - All their data is from after the 2024-03 reset, so none include the aux period.
  - They include the 2025-02 cold spell.
  - **The profile of 2025-11-18 is the best before-insulation baseline.** Part 1 of the work
    (spray foam in the crawlspace) was 2025-11-24, part 2 (the attic) was 2025-12-01.
- The first beestat profile whose year of data lies entirely after the work will be made around
  2026-12-02. Until then, use our own code for an after-insulation period (`generate()`).

## What this means for us

- **We have all the data we need.** Our database (table `ecobee_interval`) has the same ecobee
  readings for both thermostats from 2025-04-16 (from the ecobee API), plus months downloaded as CSV
  files from the ecobee website. Our readings use the same times as beestat's (local time converted
  to UTC) and the same units (beestat stores tenths of a °F and divides back).
- **Settings specific to this house, which our code copies:**
  - Both thermostats: Living Room (415533522899) and Upstairs (415522180035).
  - Living Room is set up as a heat pump (beestat: `compressor`).
  - Upstairs is not set up as a heat pump, so its heating-on time is its aux time.
  - For the Living Room, aux time after the backup heat strips were installed is real backup heat
    (in the code: after `STRIP_INSTALL_DATE`). So, as in beestat, it cancels a heating-on stretch.
- **Plan for checking our code:** run it on the 365 days up to a saved beestat profile's creation
  time (for example the Living Room profile of 2026-10-31). Then compare the bins and the straight
  lines. Expect small differences, from the "readings beestat throws away" rules and from beestat
  reading the data in pieces. (This check has been done; see "Our own code and how well it matches
  beestat".)
- **Data from before the insulation work:** beestat itself keeps only about a year of 5-minute data
  (for both thermostats its data now starts 2025-11-01). Our own 5-minute data for both thermostats
  starts 2025-04-16. So **the saved beestat profiles are the only profiles that cover the full
  winter before the insulation work** (for example the profiles from around 2025-11). Keep saving
  them.
- **Fixed periods instead of the past 365 days:** beestat's profiles always cover the past 365 days.
  So the change from the Nov/Dec 2025 work shows up only slowly, over the following year. Our own
  code can use the same method on fixed periods, for example heating season 2025-26 against 2026-27,
  or the months before and after the work. Only a run over exactly beestat's year can be compared
  directly with beestat's numbers.
- **A physics caveat:** everything-off bins are sorted by outdoor temperature only. But the indoor
  temperature (the comfort setting) also changes how fast the house cools, because heat loss grows
  with the difference between indoor and outdoor temperature. For before/after comparisons with
  different comfort settings, a rate per °F of indoor-minus-outdoor difference would be less
  distorted. Report it next to the beestat-style numbers, not instead of them.
