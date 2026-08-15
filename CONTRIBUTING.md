# Contributing

Thanks for looking. This project is maintained by one person alongside a day
job, so please read the note on response times at the bottom before you invest
a lot of work in a change.

## The most useful thing you can contribute

**An export file this tool misreads.**

Every instrument, software version and site configuration writes a slightly
different file. The reader is template driven precisely because of that, and
the templates only cover what has been seen. A file that fails to parse, or
parses into the wrong columns, is worth more than a feature request.

Open a bug report and attach the file with anything confidential removed.
Sample names, project names and client identifiers can all be replaced with
placeholders; the parser cares about the shape of the file, not its contents.
If you cannot share the file at all, paste the first fifteen lines with the
values altered but the structure kept.

## Reporting problems

Use the issue templates. There are three, and picking the right one saves a
round trip:

* **Layout** for a file that will not read, or reads into the wrong columns.
* **Verdict** for a check whose pass or fail you believe is wrong. Say what
  you expected and on what basis.
* Anything else through the general link in the issue chooser.

For a wrong verdict, please say which rule pack you ran. A result that is
correct under 6020B limits and wrong under a facility pack is a configuration
question, not a bug in the engine.

## Proposing a change

1. Open an issue first if the change is more than a fix. It saves you writing
   something that does not fit the design.
2. Fork, branch, and keep the change to one subject.
3. Add a test. Every check in the engine has one, and a change without a test
   will be asked for one.
4. Run the suite before you open the pull request:

   ```bash
   pip install -e ".[dev]"
   pytest
   ```

5. Describe in the pull request what the change does and why, not only what
   you edited.

## On the basis for checks

`docs/SOURCE_BASIS.md` records where each check comes from and what it does
not cover. If you add or change a check, add it there too. Deliberately, that
file does not cite clause numbers, because they move between revisions of the
standards and a stale citation is worse than none. Describe the requirement
instead.

## Scope

This tool reviews a batch after the run. It does not reprocess data, does not
rebuild calibrations to replace the vendor software, and does not decide
whether a result is fit for a particular purpose. Those are the analyst's job,
and proposals to automate them will be declined.

## Response times

Issues and pull requests are read, but replies may take a week or two. If
something has gone quiet for longer than that, a comment on the thread is a
reasonable nudge rather than a nuisance.

## License

Contributions are accepted under the MIT license that covers the project.
