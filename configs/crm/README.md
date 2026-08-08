# CRM library

One YAML file per certified reference material. `crm_recovery` loads every
`*.yaml` in this directory, matches each against the sample names in a batch,
and checks recovery **per certified element**.

## Why this exists

Every other recovery check divides by `Sample.level` — the single expected
concentration the export carries in its Level column. That is the right shape
for a spike made from one multi-element standard, and the wrong shape for a
reference material: a CRM certifies dozens of elements at dozens of *different*
values, and the export knows none of them. The values have to come from the
certificate, so they live here — versioned in git next to the rule packs,
reviewable like any other QC policy.

## Schema

```yaml
id: nist_srm_1640a               # snake_case; defaults to the filename
name: NIST SRM 1640a             # shown in the report
matrix: water                    # optional, free text
source: "https://…/certificate"  # where the values came from — cite it
unit: ppb                        # unit of EVERY value under `certified:`

default_value_type: certified    # applies to entries that don't say (below)
provenance:                      # required in spirit for compilation values
  compilation: GeoReM            # "GeoReM" | "USGS" | "NIST certificate" | …
  version: "2024-01"             # data set / certificate revision
  accessed: "2026-08-03"
  citation: "…"

match:
  name_patterns:                 # Python regex, searched against sample names
    - '(?i)\b1640\s*-?\s*a\b'
    - '(?i)\bSRM1640A\b'

certified:
  As: { value: 8.075, uncertainty: 0.070 }
  Cd: { value: 3.992, uncertainty: 0.074, type: reference }
  Ag: { value: 0.03,  type: information }   # reported, never decisive
  Pb: 12.005                     # bare number = value, default type
  Zn: { value: }                 # placeholder: skipped and announced, not checked
```

### Value types

Not every number in a compilation carries the same authority, and a geological
compilation says so explicitly. The distinction decides whether a value may fail
someone's batch:

| `type` | meaning | can FAIL a batch |
|---|---|---|
| `certified` | certificate of analysis, metrologically traceable | yes |
| `reference` | compilation "preferred"/reference value (GeoReM, USGS) | yes |
| `information` | indicative; may rest on a single lab's measurement | **no** — reported only |

### Provenance

A certificate identifies itself; a compilation does not. GeoReM's preferred value
for a material changes between data set releases, so a report that cannot name
the release it used cannot be re-derived. Fill `provenance` completely for any
compilation value.

### Placeholders

`Zn: { value: }` is a legitimate, expected state — a lab transcribes the elements
it measures and leaves the rest. Unfilled elements are skipped **and listed in the
report**, so a PASS is never mistaken for "everything was checked". A file with no
usable value at all is an error, not a silent no-op.

## Skeletons for geological reference glasses

`*.yaml.example` files here are pre-built skeletons for the materials used in
LA-ICP-MS trace-element work — NIST SRM 610/612 and the USGS glasses BCR-2G,
BHVO-2G, BIR-1G, GSD-1G. Each carries the standard 47-element suite with every
value empty.

Fill the elements you measure, complete `provenance`, then **rename to `.yaml`** —
only then is the file loaded.

> The `-2G`/`-1G` suffix is not decoration: **BCR-2G (glass) is a different
> material from BCR-2 (powder)**, with different preferred values. The shipped
> name patterns require the `G`. Do not loosen them.

Units are converted at check time (`ppb`/`ppm`/`ppt`, `µg/L`, `mg/L`, `ng/g`, …).
An unrecognized unit produces a NOT-ASSESSED row with the reason — a certificate
in mg/L read against results in ppb is the easiest way to be wrong by 1000×, so
icpms-qc refuses to guess.

`uncertainty` is optional and **informational**. It is the source's own
uncertainty (expanded k=2 on a certificate, usually a 1s/2s spread in a
compilation) and describes the material, not your lab's acceptance criteria — a
±1% uncertainty is far tighter than any method's recovery window. The pass/fail
window comes from the rule pack:

```yaml
crm_recovery:
  enabled: true
  params: { window_pct: [80, 120] }
```

The report shows both: recovery against the window (decides pass/fail) and
whether the result also lands inside the certificate's uncertainty (context).

## Adding your material

1. Copy `example_synthetic_water.yaml`.
2. Transcribe the values from the certificate of analysis. NIST SRM, ERM/BCR,
   NRC-CNRC and High-Purity certificates are all published openly — cite the URL
   in `source:` so the next reviewer can check your transcription.
3. Add the name patterns your lab actually types into the sequence.
4. Run `icpms-qc check` and confirm the report names your material.

Only certified values belong in a file here. Information about your samples,
your clients or your batches does not — this directory is meant to be shareable,
and a contributed CRM file is one of the most useful things you can send back
upstream.

**`example_synthetic_water.yaml` is fabricated demo data.** It exists so the
test suite and the demo batch have something to match. It is not a real material
and its numbers mean nothing.
