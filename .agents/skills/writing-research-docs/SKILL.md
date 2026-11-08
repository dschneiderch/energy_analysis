---
name: writing-research-docs
description: Use when writing or revising a document under docs/research/ in this repository, or when documenting a method, a data-handling rule, a threshold, a validation of code, a sensitivity test, or a data-quality finding for the house's energy data.
---

# Writing research docs

## Overview

A research doc (`docs/research/<topic>.md`) explains **how**: how data is read, which rules and
thresholds a method uses, and how we checked that the code works. It never says what the results
mean for the house; that belongs in a summary doc (skill `writing-summary-docs`). The reader is the
homeowner, not a programmer.

## Which doc gets what

| Content | Goes in |
| --- | --- |
| How data is read, cleaned, thrown away; algorithms, rules, thresholds | research (this skill) |
| Checks that the code works (matches beestat, a stand-in vs the real data) | research |
| Tests of a method choice (for example 15 vs 30 minutes) | research |
| Which data is usable, data-quality history | research |
| Results that answer a question about the house, interpretation, conclusions, the user's impressions, withdrawn results, next steps for the question | summary (`writing-summary-docs`) |

When a method choice rests on a result, write one sentence and point to the summary section by its
heading.

## Order

1. **Title, date, and purpose**: what the method is for.
2. **Output first**: what the method produces and how to read it.
3. **Input handling**: which data it uses, what it throws away, how it decides. Give a small worked
   example (a table of a few readings) for any rule a reader could misread.
4. **Checks**: how the method was validated, with the numbers.
5. **Limits**.
6. **Reference**: field and column tables, and a short "where in the code" list.

Code identifiers appear only in backticks inside tables, brackets and the reference section. The
prose must read correctly without them.

## Writing rules

(Shared with `writing-summary-docs`; change both together.)

- Plain English, short sentences. Explain an unavoidable term the first time it appears.
- Every number and claim comes from data, notes or code you checked. Label your own arithmetic or
  inference ("derived", "not checked"), or leave it out.
- If a note is terse enough to fit more than one thing (which login, which thermostat, which
  period), find the answer in the code or data before writing it down. If you can't, quote the
  note's own words instead of choosing a reading.
- Write down the sign convention, then check each interpretation against it. For example, a
  heating-off rate going from −0.28 to −0.39 °F/h means *faster* heat loss.
- An arrow (→) means before → after, nothing else. Write any other comparison with both sides
  labeled ("winter −0.28 vs spring and fall −0.39 °F/h").
- Copy numbers exactly and keep caveats.
- Markdown table separators: `| --- |`.
- After moving content, update every link to it; docs refer to each other by section heading.

## Common mistakes

| Mistake | Fix |
| --- | --- |
| The doc ends with "Results" or "Conclusion" about the house | Move them to the summary; leave a one-line pointer |
| A guessed cause ("probably defrost") written as fact | Check it, or mark it "not checked" |
| A sign read backwards | Compare against the written sign convention |
| A bare arrow between two seasons or two rooms, read as before → after | Label both sides; keep arrows for before → after |
| Prose that only makes sense if you know the function names | Say what the code does in plain words; put the names in the reference section |
