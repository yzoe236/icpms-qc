# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.1] - 2026-08-14

First version published to PyPI. Install with `pip install icpms-qc`.

### Fixed

* Sample names taken from the Element's recorded data-file path were wrong on
  Linux and macOS. That path is written by the instrument software, which runs
  on Windows, so it is a Windows path wherever the tool itself runs; reading it
  with `pathlib.Path` on a POSIX host left the whole string intact and a sample
  came out named `E:\Data\Citro\10 ppb`. It is now read as a Windows path.

### Changed

* The package summary says ICP-OES as well as ICP-MS, which has been true since
  the ICP Expert reader landed.

### Added

* Continuous integration running the suite on Python 3.10 through 3.13, which
  is what caught the path bug above on its first run.
* `CITATION.cff`, so GitHub offers a Cite this repository button.
* `CONTRIBUTING.md`, `paper.md` and `paper.bib`.
* `docs/RELEASING.md`, and a publish workflow using PyPI trusted publishing,
  so releasing needs no API token.

## [0.1.0] - 2026-08-14

First published version.

### Added

* Template-driven reader for Agilent MassHunter batch exports, covering
  single and two-row headers, concentration and count files exported as a
  pair, and cp1252 fallback for files that are not UTF-8.
* Reader for Agilent ICP Expert workbooks, bringing optical emission batches
  through the same review.
* QC engine with rule packs: an EPA 6020B style pack, a 200.8 style pack, and
  a plainer facility pack.
* Calibration checks: linearity, per-level back-calculation, and a
  heteroscedasticity diagnostic. Back-calculation is separate from linearity
  because a correlation coefficient is dominated by the high end of the curve,
  so a bottom standard reading half its true value can still report r above
  0.999.
* Continuing calibration verification, internal standard, duplicate and blank
  checks.
* Emission line agreement check for optical emission, which separates random
  scatter from spectral interference and names the line at fault when one
  reads consistently high across samples.
* HTML report for reading and JSON report for scripts.
* Command line interface exiting 0 on pass, 2 on fail and 1 on error, so it
  can be dropped into a pipeline.
* `docs/SOURCE_BASIS.md` recording the basis of each check, and the known
  gaps, without citing clause numbers that go stale between revisions.

### Notes

145 tests pass on this tag. The Development Status classifier is Pre-Alpha:
this has been run against real batches, but by one person and on two
instrument families.

[Unreleased]: https://github.com/yzoe236/icpms-qc/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/yzoe236/icpms-qc/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/yzoe236/icpms-qc/releases/tag/v0.1.0
