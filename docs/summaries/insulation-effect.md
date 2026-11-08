# Did the insulation work reduce heat loss?

Status as of 2026-11-08. Methods are described in `docs/research/beestat-temperature-profiles.md`
and `docs/research/house-profile-options.md`; this note only reports and interprets results.

## The work

| Part | Date | What | Where we expect to see it |
| --- | --- | --- | --- |
| 1 | 2025-11-24 | Spray foam on the crawlspace walls and rim joist | Basement and crawlspace sensors; the Living Room zone above |
| 2 | 2025-12-01 | Attic | The three Upstairs rooms (Bedroom, Kids' Room, Office) |

Before = data up to 2025-11-23. After = data from 2025-12-02. Order confirmed on the calendar
2026-11-07. The homeowner's impression is that the Upstairs rooms lose heat more slowly since the
attic work.

## Short answer

- **Upstairs probably loses heat a little more slowly after the attic work, about 15 %.** The best
  comparison (winter against winter) points that way for every room, but the change is still
  within the rough uncertainty.
- **Nothing is proven yet.** Comparisons based on beestat's own profiles show no change beyond
  normal year-to-year variation. The winter after the work (2025–26) was very mild, so the cold end
  can't be compared at all.
- **The strongest test hasn't been run yet:** heat-pump energy use against weather (Emporia), which
  covers two winters before the work.

All numbers below are the heating-off rate: how fast the indoor temperature falls (°F per hour)
with everything off, averaged over outdoor temperatures both periods share, up to 55 °F.
**Positive change = slower heat loss.** "±" is a rough uncertainty (2 standard errors across the
one-degree temperature slots); treat it as optimistic.

## 1. Winter against winter, Upstairs rooms (best comparison)

December 1 – April 15, before (2024–25) and after (2025–26). Same months, same method on both
sides. The 2024–25 winter uses room readings from the ecobee website downloads and decides "heat
pump off" from hourly Emporia power, because there is no Living Room thermostat data for it.

| Upstairs | Before → after (°F/h) | Measured stretches |
| --- | --- | --- |
| 3-room average | −0.40 → −0.34 (+0.06 ± 0.06; 27–55 °F) | 422 → 657 |
| Bedroom | −0.35 → −0.23 (+0.12 ± 0.07) | |
| Kids' Room | −0.45 → −0.36 (+0.08 ± 0.08) | |
| Office | −0.47 → −0.40 (+0.06 ± 0.07) | |

- About 15 % slower heat loss for the 3-room average. Every room points the same way; the Bedroom
  most clearly.
- With beestat's longer 30-minute minimum for each heating-off stretch (fewer stretches: 296 → 427)
  the 3-room average read +0.09 ± 0.07. The larger sample shrank it, so part of that was noise.
- Only outdoor temperatures from 27 °F up can be compared. In the 2024–25 winter the heat pump was
  never off for a whole hour below 10 °F, and rarely below 30 °F.
- There is no second winter before the work with room readings, so natural year-to-year variation
  for this comparison is unknown.

## 2. Heating season, our 5-minute data

October–May only. Before: 2025-04-16 to 2025-05-31 and 2025-10-01 to 2025-11-23. After:
2025-12-02 to 2026-05-31 and 2026-10-01 to 2026-11-07.

| Heating-off rate | Before → after (°F/h) | Measured stretches |
| --- | --- | --- |
| Upstairs 3-room average | −0.44 → −0.35 (+0.09 ± 0.03; 32–55 °F) | 2,307 / 4,426 |
| Upstairs Bedroom | −0.30 → −0.24 (+0.07 ± 0.05) | 1,158 / 2,014 |
| Upstairs Kids' Room | −0.52 → −0.47 (+0.06 ± 0.05) | same |
| Upstairs Office | −0.38 → −0.33 (+0.05 ± 0.05) | same |
| Upstairs thermostat's own reading | −0.53 → −0.47 (+0.05 ± 0.03) | 1,152 / 1,936 |
| Living Room thermostat | −0.85 → −0.69 (+0.16 ± 0.11; 35–55 °F) | 1,025 / 1,915 |

- Taken at face value: about 20 % slower heat loss Upstairs and 19 % in the Living Room.
- **But this comparison mixes seasons.** "Before" is spring and fall only; "after" includes
  December–March. Before the work, Upstairs winter alone read 0.11 °F/h *slower* heat loss than
  spring and fall at the same outdoor temperatures (winter −0.28 vs spring and fall −0.39 °F/h,
  ± 0.04). That season effect is as
  large as the change above, so this comparison overstates the insulation effect.
- Comparing the whole 2024–25 winter with the full after heating season gives only +0.01 ± 0.06,
  because that comparison mixes seasons the other way.
- **Heating speed** (how fast the house warms while heating) can't be judged yet. Upstairs has only
  59 heating stretches before the work; its room-average heating rate came out 0.47 ± 0.43 °F/h
  lower after. The Living Room came out +0.28 ± 0.35 °F/h. Both are within noise.

## 3. beestat's own profiles

beestat's profile of 2025-11-18 (the year before the work) against our own year after the work
(2025-12-02 to 2026-11-07), compared with the difference between two beestat profiles from before
the work:

| Profile | Before → after | Variation: 2024-11-19 → 2025-11-18 | Variation: 2025-03-01 → 2025-11-18 |
| --- | --- | --- | --- |
| Living Room heating off | −0.90 → −0.88 (+0.02; 24–55 °F) | +0.10 | +0.09 |
| Upstairs heating off | −0.84 → −0.74 (+0.10; 21–55 °F) | −0.15 | −0.07 |
| Living Room heating on | +3.12 → +3.30 (+0.17; 19–55 °F) | +0.41 | +0.16 |
| Upstairs heating on | +2.49 → +2.46 (−0.03; 20–47 °F) | −0.55 | −0.17 |

- Every change is within the variation between the two before-work profiles, even against the
  2025-03-01 profile, which shares a winter with the baseline and so understates variation.
- The Upstairs rows are less trustworthy: beestat's way of reading both thermostats biases
  Upstairs heating-off results by 0.03–0.14 °F/h, and the bias can't be removed for the archived
  profiles (method doc, "The Upstairs problem").

## Withdrawn result

A first look compared the slopes of the whole fitted lines, April–October 2025 against
April–October 2026, and showed 16 % (Living Room) and 23 % (Upstairs) lower heating-off slopes.
That was a side effect of fitting lines across 20–95 °F, where summer sun dominates, not a real
change. Don't use whole-line slopes for before/after comparisons.

## What would settle it

- **Emporia energy against weather** (option 3 in `house-profile-options.md`): covers the winters
  2023–24 and 2024–25 before the work, independent of thermostat setup problems.
- **Winter 2026–27**, ideally a cold one, as a second after-work winter.
- **Crawlspace and basement sensors** for part 1: data from 2025-04-21, plus a Living Room download
  for 2024-11-01 to 2024-12-02. The week 2025-11-24 to 2025-11-30 (crawlspace done, attic not yet)
  could isolate the crawlspace, but a week is short.
- The first beestat profile covering a full year after the work arrives around 2026-12-02.
