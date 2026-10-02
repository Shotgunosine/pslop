# pmap-calibrate

A command-line tool for HHS Calibration Board members. It checks whether an employee's proposed
FY 2026 performance ratings are supported by their self-accomplishment narrative, measured
against the performance plan's written standards and the three HHS calibration factors
(Organizational Value, Quality & Reliability, Timeliness & Efficiency).

It uses [Jev](https://openrouter.ai/docs/guides/community/jev) (`typesafe/jev-1.13`) through
OpenRouter's Decisions API. Jev is a decision model, not a chat model: it answers typed
questions with probabilities and writes no text. Following TypeSafe's guidance, the tool asks Jev
narrow, literal questions and keeps counting, thresholds, and the rating rule in code.

## How a rating is checked

There are two modes. **Detailed** (the default) asks about each narrative item against each
standard. **Simple** (`--mode simple`) asks once whether the whole narrative meets each element's
standards. In both modes, proposed ratings are never sent, so they can't anchor the answers.

### Detailed mode

1. **The plan is parsed** into elements and their Level 3 and Level 5 standards, one standard per
   bullet or sentence. Standards with a number ("more than two research projects", "at least four
   projects") are marked as counted. Check the result with `show-plan`.
2. **The narrative is split into items**, one per bullet or sub-bullet. The employee's `[OV, QR, TE]`
   tags are removed from the text Jev sees and kept for comparison.
3. **Each item is sent to Jev on its own.** For each standard Jev gives the probability that the
   item is an example of it. For counted standards, Jev instead picks how many instances the item
   describes, from none to five or more. Jev also judges whether the item shows each calibration
   factor, states a concrete result, makes the employee's own role clear, and whether it addresses
   the reader. **Proposed ratings are never sent**, so they can't anchor the answers.
4. **The rule, in code:**
   - A standard is met when at least one item is an example of it with probability ≥ `--threshold`
     (default 0.5).
   - A counted standard is met when the instances in the employee's own items add up past its
     number. A bullet and its sub-bullets count once, as the larger of the parent's count and the
     children's total.
   - An element is supported at **Level 5** when at least `--level5-share` (default 50%) of its
     Level 5 standards are met, and at **Level 3** otherwise. A narrative alone never establishes
     Level 1; a weak Level 3 showing is reported as a warning.
   - The summary score follows HHS: the unweighted mean of element levels, where Level 5 is
     4.60–5.00 and Level 3 is 3.00–4.59.
5. **Each element and the summary are reported as Supported, Not supported, or Possibly
   under-rated.** A result is marked **borderline** when standards close to the threshold (within
   0.15), or counts within one of the number, could change it.

Every met standard lists the items that support it, with their probabilities, so the board can
check the evidence. The JSON output keeps every raw Jev answer.

### Simple mode

The whole narrative (plus the rating official's narrative, if given) is sent in one request. For
each element, Jev answers two yes/no questions: does the narrative, taken as a whole, meet the
element's Level 5 standard, and does it meet the Level 3 standard? Each question includes the full
text of that level's standard. Jev also judges whether the narrative shows each calibration factor.

- An element is supported at **Level 5** when P(Level 5 met) ≥ `--threshold`, and at **Level 3**
  otherwise. So a proposed 5 is supported when P(Level 5 met) passes. A proposed 3 is supported
  unless P(Level 5 met) also passes, in which case it is flagged as possibly under-rated.
- It is **borderline** when P(Level 5 met) is within 0.15 of the threshold.
- A low P(Level 3 met) is reported as a warning, not as Level 1.

Simple mode is quicker to read and about 40 times cheaper. It gives no per-standard evidence, and
it leaves counted standards ("more than two projects") to Jev, which TypeSafe says does not count
reliably. The tool warns when a plan has counted standards. A useful pattern is to run simple mode
for everyone, then detailed mode for borderline cases and anything the board disputes.

### Calibrate the rule before relying on it

The defaults are a starting point, not policy. TypeSafe recommends choosing thresholds from a
labeled sample. Run a few narratives the board has already agreed on, compare the results, and
adjust `--threshold` and `--level5-share` until they match the board's judgment. Then use the same
settings for everyone; they are recorded in each report's audit block. Because a standard needs
only one supporting item, a low threshold makes standards easy to meet in long narratives.

## Setup

```sh
pixi install
```

`.pixi/config.toml` tells pixi to use the macOS certificate store, which the NIH network needs
because it re-signs TLS traffic to some hosts. The tool's own requests use the system store too,
via `truststore`.

### API key

The tool reads `OPENROUTER_API_KEY` from the first place it finds one:

1. the environment (`export OPENROUTER_API_KEY=...`);
2. the file given with `--env-file PATH` (a global option, before the command name), or else
   `./.env`, or else `~/.config/pmap-calibrate/env`.

```sh
mkdir -p ~/.config/pmap-calibrate
cp .env.example ~/.config/pmap-calibrate/env    # then edit it and paste your key
chmod 600 ~/.config/pmap-calibrate/env
pixi run pmap-calibrate check-model             # sends made-up data only; confirms key, routing, answer types
```

`.env` is listed in `.gitignore`. To use a different model, set `PMAP_MODEL` in the same file or
the environment, or pass `--model`. `~typesafe/jev-latest` tracks new releases, but pinning
`typesafe/jev-1.13` keeps results comparable across a calibration cycle.

## Commands

```sh
pixi run pmap-calibrate show-plan plan.md -r "Customer Experience=5" -r "Training=3"   # local
pixi run pmap-calibrate names narrative.md > names.txt                                 # local
pixi run pmap-calibrate assess narrative.md --plan plan.md \
  -r "Customer Experience=5" -r "Scientific Research=5" -r "Presentations=5" \
  -r "Training=5" -r "Service=3" --names names.txt --out report.md --json report.json
pixi run pmap-calibrate batch manifest.csv --out-dir reports/
```

- `-r ELEMENT=LEVEL` takes levels 1, 3, or 5. Short names are matched to the plan's element names;
  an unmatched name is an error.
- `--supervisor FILE` adds the rating official's narrative as evidence. It is not counted toward
  numbered standards, to avoid counting the same work twice.
- `--mode simple` uses the whole-narrative check described above; `--mode detailed` is the default.
- `--bargaining-unit` notes that writing to the calibration factors is optional for this employee.
- `--dry-run` shows the items that would be sent and estimates the cost, without contacting
  OpenRouter. `--save-requests FILE` writes the exact request bodies as JSON lines.
- Inputs can be `.md`, `.txt`, `.docx`, or `.pdf`. Plans need a heading (or a "Critical Element"
  line) per element, followed by "Level 3 – Fully Successful" and "Level 5 – Outstanding" sections.
  Narratives work best as bullets. In a bulleted narrative, paragraphs such as a factor key are
  skipped, with a warning.

### Batch manifest

```csv
employee_id,narrative,plan,ratings,names,supervisor,employee_name,bargaining_unit
E001,e001/narrative.docx,plans/mlc.md,"Customer Experience=5; Scientific Research=5; Presentations=5; Training=5; Service=5",e001/names.txt,,,no
E002,e002/narrative.pdf,plans/mlc.md,"Customer Experience=3; Scientific Research=5; Presentations=3; Training=3; Service=3",,e002/supervisor.md,,yes
```

`employee_id`, `narrative`, `plan`, and `ratings` are required, and paths are relative to the
manifest. The tool writes a `.md` and a `.json` report per employee, plus `summary.csv`. The
console summary is sorted by each employee's Level 5 evidence: the share of Level 5 standards met
across all elements (detailed mode), or the mean P(Level 5 met) across elements (simple mode). It also shows how many Level 5 summaries were proposed vs. supported, since HHS caps
Level 5 ratings.

## Privacy

- **Pseudonymization is optional.** By default, text is sent as written. Pass `--names FILE` (one
  name per line) and/or `--employee-name` to replace names with `[PERSON_1]`, `[EMPLOYEE]`, and so
  on before anything is sent. Names missing from the list are sent unchanged. Reports always show
  the original text.
- Every request asks OpenRouter to use only providers that don't store or train on prompts
  (`data_collection: deny`) and, unless you pass `--no-zdr`, only zero-data-retention endpoints.
- Before using real employee records, confirm with your component's privacy officer that
  OpenRouter and TypeSafe are approved for this data.

## Cost and limits

Each narrative item is one request, with roughly 60–70 questions for a five-element plan. A
24-item narrative is about 24 requests and a few hundred thousand input tokens. At the listed
$0.042 per million input tokens, that is around a cent per employee; output tokens are free.
Requests run 8 at a time, and rate limits (429), overloads, and transient errors are retried with
backoff. Simple mode is one request of a few thousand tokens per employee, a small fraction of a
cent.

## Development

```sh
pixi run test
```

Question wording is in `src/pmap_calibrate/questions.py`; bump `QUESTION_VERSION` when you change
it. The rule is in `src/pmap_calibrate/decide.py`.
