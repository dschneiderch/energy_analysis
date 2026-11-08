---
name: writing-summary-docs
description: Use when writing or revising a document under docs/summaries/ in this repository, or when writing up results, an interpretation, or the answer to a question about the house's energy data.
---

# Writing summary docs

## Overview

A summary doc (`docs/summaries/<question>.md`) answers **one question**: what we found and what it
means. It never explains how a method works; that belongs in a research doc (skill
`writing-research-docs`), which the summary links to. The reader is the homeowner, not a
programmer.

## Which doc gets what

| Content | Goes in |
| --- | --- |
| Results that answer a question about the house, interpretation, conclusions, the user's impressions, withdrawn results, next steps for the question | summary (this skill) |
| How data is read, cleaned, thrown away; algorithms, rules, thresholds | research (`writing-research-docs`) |
| Checks that the code works, tests of a method choice, which data is usable | research |

If a result needs a method detail to make sense, say its effect in one sentence and link to the
research doc section by its heading.

## Order

1. **Title** = the question.
2. **Status as of** date, and links to the research docs that describe the methods.
3. **Context**: what prompted the question; the relevant events and dates; where we expect to see
   an effect.
4. **Short answer**: 2–4 bullets, verdict first, and how sure we are.
5. **How to read the numbers**: units, what positive and negative mean, what "±" is.
6. **One section per analysis, strongest first**: one or two sentences on what was compared, a
   table, then 2–4 bullets on what it means, caveats included.
7. **Withdrawn result**, if any: what was claimed and why it was wrong.
8. **What would settle it**: the additional work, data or time needed.

Name things in plain words ("heating-off rate", "the Living Room thermostat"). The summary contains
no function names, table or column names, code, or formulas.

## Writing rules

(Shared with `writing-research-docs`; change both together.)

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
| The summary explains how the code picks data, with function names | Move it to the research doc; describe the effect in a sentence |
| No context or next-steps section | Follow the 8-part order |
| A fact from terse notes attached to the wrong thing (the "unofficial login" belongs to the API, not the website downloads) | Look it up in the code before writing |
| A sign read backwards | Compare against the written sign convention |
| A bare arrow between two seasons or two rooms, read as before → after | Label both sides; keep arrows for before → after |
