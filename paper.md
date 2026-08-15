---
title: 'icpms-qc: post-run quality control review for ICP-MS and ICP-OES batch exports'
tags:
  - Python
  - analytical chemistry
  - mass spectrometry
  - optical emission spectrometry
  - quality control
  - method validation
authors:
  - name: Linhan Li
    orcid: 0000-0000-0000-0000
    affiliation: 1
affiliations:
  - name: Independent researcher
    index: 1
date: 14 August 2026
bibliography: paper.bib
---

# Summary

Inductively coupled plasma mass spectrometry (ICP-MS) and inductively coupled
plasma optical emission spectrometry (ICP-OES) are the routine techniques for
measuring trace elements in water, soil, digested solids and biological
material. Instruments are run in batches: calibration standards, then samples
interleaved with quality control samples that are measured for the sole purpose
of showing whether the measurement was still behaving. Before any result leaves
the laboratory somebody has to read those quality control samples and decide
whether the batch holds.

`icpms-qc` does that reading. It takes the file the instrument software already
exports, works out what the file is, applies a configurable pack of acceptance
rules to the calibration, the continuing calibration verification, the internal
standards, the duplicates and the blanks, and writes a report stating whether
the batch can be reported and, when it cannot, which measurement failed and by
how much. The report is produced as HTML for a person and as JSON for a script.
The command line tool exits 0 when everything passes, 2 when quality control
fails and 1 on error, so it can sit inside an automated pipeline rather than
beside one.

# Statement of need

Instrument vendors ship quality control features, but they are tied to one
vendor's software, they are configured per sequence rather than per method, and
what they check is not always what a reporting requirement asks for. The gap is
concrete rather than hypothetical. In the ICP Expert workbook this tool was
first tested against, the software's own quality control worksheet contained
zero rows; the response at that laboratory had been to write 564 lines of
single-use Python to rebuild the calibration, subtract the blank and compare
the results, for one batch.

That response is the normal one, and it is why the surrounding software
ecosystem has not accumulated. A search of GitHub in August 2026 returned 181
repositories mentioning ICP-MS, none of which had more than 14 stars: they are
single-instrument, single-project scripts, written because nothing reusable
existed and unusable by anyone else because each is bound to one export layout.
Neighbouring fields show what the alternative looks like. Proteomics has an
open interchange format, mzML [@martens2011mzml], and a common vendor converter,
ProteoWizard [@chambers2012proteowizard], and on that foundation a large tool
ecosystem has grown. Elemental analysis has neither.

`icpms-qc` takes the narrower first step of making the review itself reusable.
The reader is template driven, so support for a new export layout is a
configuration file rather than a code change, and the rule packs are separate
from the engine, so a laboratory reporting under one requirement and a
laboratory reporting under another run the same code against different limits.
Packs modelled on EPA Method 6020B [@epa6020b] and EPA Method 200.8 [@epa200_8]
are included alongside a plainer in-house pack.

Two checks exist because summary statistics hide the failures they describe.
The first is per-level back-calculation. Laboratories commonly accept a
calibration on its correlation coefficient, but that statistic is dominated by
the high end of the curve, and a bottom standard recovering at half its prepared
value can coexist with an r above 0.999. Calibration guidance has long argued
that linearity should be judged on residuals rather than on r
[@eurachem2014fitness]. The tool reports each calibration level back-calculated
against its own curve, and flags the levels that miss, which in practice is the
low end where the reporting limit lives. A companion diagnostic reports whether
relative error is systematically larger at low concentration than high, the
signature of fitting heteroscedastic data without weighting, which is the
ordinary condition of an elemental calibration.

The second is specific to optical emission. An element is usually measured on
several spectral lines at once, and those lines should agree. When they do not,
the useful question is which kind of disagreement it is: scatter is imprecision,
while one line reading consistently high across every sample is another
element's emission falling on that wavelength. The tool separates the two by how
often one line is the higher of the pair, and names the interfering line rather
than reporting an unattributed disagreement.

The intended users are analysts and laboratory managers who need a defensible,
repeatable record that a batch was reviewed, and researchers who want the same
review applied consistently across a study rather than by hand per run. The
basis of each check, and the gaps that remain, are recorded in the repository
so that a reviewer can see what the tool does and does not claim.

# Acknowledgements

The author thanks the analysts whose real batches exposed the failure modes
that the automated checks were subsequently written to catch.

# References
