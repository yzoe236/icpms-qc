# Releasing

## Why there is no API token here

PyPI accepts uploads authenticated by an API token, and it also accepts uploads
authenticated by GitHub itself through OpenID Connect, which PyPI calls
[trusted publishing](https://docs.pypi.org/trusted-publishers/). This project
uses the second, because a token is a long-lived secret that has to be stored
somewhere, pasted somewhere, and rotated when either of those goes wrong. The
OIDC exchange happens between GitHub and PyPI at the moment of upload and
leaves nothing behind to leak.

If a token was ever created for this project, revoke it. PyPI account settings,
API tokens, revoke. There is nothing here that needs one.

## One-time setup on PyPI

Do this once, before the first release.

1. Sign in to PyPI and go to **Your projects**, or, if `icpms-qc` has never been
   uploaded, go to **Publishing** in account settings and add a *pending*
   publisher.
2. Fill in exactly:

   | Field | Value |
   |---|---|
   | PyPI project name | `icpms-qc` |
   | Owner | `yzoe236` |
   | Repository name | `icpms-qc` |
   | Workflow name | `publish.yml` |
   | Environment name | `pypi` |

3. In the GitHub repository, go to **Settings, Environments** and create an
   environment named `pypi`. Adding yourself as a required reviewer there is
   worth doing: it means an upload waits for a click rather than happening the
   instant a release is published.

The environment name has to match on both sides or PyPI rejects the upload.

## Cutting a release

1. Update `version` in `pyproject.toml`. Nothing derives it, so it is the one
   place it lives.
2. Add the version's section to `CHANGELOG.md`.
3. Commit, then tag and push:

   ```bash
   git tag -a v0.1.1 -m "icpms-qc 0.1.1"
   git push origin master
   git push origin v0.1.1
   ```

4. Create the GitHub Release from that tag. Publishing the release triggers
   `publish.yml`, which builds, runs `twine check`, and uploads.

A version number on PyPI can never be reused, even after deleting the release,
so check what is in `dist/` before the upload runs. The build job runs first
and its artifact is what gets uploaded, so a failure there stops the upload.

## Zenodo

Zenodo archives GitHub releases and mints a DOI for each one, but only for
releases created **after** the GitHub to Zenodo hook is switched on. Enabling it
does not reach back for earlier releases, so `v0.1.0` will not get a DOI
retroactively; the first DOI will belong to whichever release comes after the
hook is enabled.

Turn it on at Zenodo, account settings, GitHub, then flip the switch for this
repository. `.zenodo.json` in the repository root supplies the title,
description, authors and keywords, so the record does not depend on what Zenodo
can scrape.
