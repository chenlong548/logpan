# logpan

A lightweight log scanner for Windows hosts, written in pure Python (stdlib only, zero dependencies). It is the log-triage companion to [DKail](https://github.com/chenlong548/DKail): when DKail flags something, logpan is how you go look at what the logs actually say.

logpan reads log files (or stdin), matches lines by keyword or regex, and reports what it found — per-pattern hit counts, first/last positions with extracted timestamps, context lines, a Top-N summary across files — with JSON export for anything downstream.

## Why another grep

Because the job is not searching, it is summarizing. `findstr` and `grep` tell you *where* a pattern matches; logpan tells you *what the log is doing*:

- **Per-pattern counts and boundaries** — how many times `ERROR` fired, at which lines, between which timestamps
- **Timestamp extraction** — recognizes `2026-10-13 10:00:01`-style timestamps (with optional millis/timezone) and anchors hits to them
- **Top-N pattern ranking** across a whole directory tree in one pass
- **Encoding tolerance** — falls back through `utf-8` → `gbk` → `latin-1` per line, so mixed-encoding logs (common on Chinese-locale Windows) scan without dying
- **Machine-readable output** — `--json` for pipelines, `-o` to write it to a file

It is a single-file tool on purpose. Copy `logpan.py` onto a host and run it; nothing to install.

## Usage

```bash
# all ERROR and timeout lines in one file (keywords are case-insensitive)
python logpan.py -f app.log -k ERROR -k timeout

# regex over several files, with 20 lines of context before each hit
python logpan.py -f a.log b.log -r "ERROR|FATAL" -c 20

# recursive scan of a directory, only *.log files, JSON export
python logpan.py -f logs/ --glob "*.log" -k Traceback --json -o report.json

# pipe from stdin
cat app.log | python logpan.py -k Traceback -
```

### Options

| Option | Meaning |
|---|---|
| `-f/--file` | files, directories (recursive), or glob patterns; `-` = stdin (required, repeatable) |
| `-k/--keyword` | literal keyword, case-insensitive (repeatable) |
| `-r/--regex` | Python regular expression (repeatable) |
| `-c/--context` | lines of context **before** each hit (default 0) |
| `--glob` | filename filter when scanning directories, e.g. `*.log` |
| `-t/--top` | Top N patterns shown in the summary (default 5) |
| `-v/--verbose` | print first/last hit line per pattern |
| `--json` | JSON output (add `--hits` to include full hit details) |
| `-o/--output` | write the JSON to a file instead of stdout |
| `--tail` | show last N lines of files with no hits (default 50, `0` disables) |

Exit codes: `0` hits found (or `--json` requested), `1` no hits, `2` usage error.

## Python API

The CLI path (`scan_stream`) tracks first/last positions and context. A thinner layer is exposed for reuse and testing:

```python
import logpan

logpan.scan_lines(lines, "ERROR")                  # -> list of matching lines (case-sensitive substring)
logpan.scan_lines(lines, r"ERROR|WARN", regex=True)
logpan.scan_file("app.log", "ERROR")               # "-" = stdin; missing file raises FileNotFoundError
logpan.compile_pattern(r"err\d+", regex=True)      # -> (label, compiled, is_regex)
```

`compile_pattern` raises `ValueError` on an empty pattern and propagates `re.error` on an invalid one — it never swallows either.

## Scope and limits

Stated plainly, in keeping with how [our other tools](https://github.com/chenlong548) are published:

- Single-pass line scanner. It does not tail files, ship logs anywhere, or correlate across hosts. If you need that, that is DKail's job, not this file's.
- Context is before-only (`-c`), which is what triage usually wants; after-context is not implemented.
- Timestamps are extracted, not parsed into time zones — the string is the truth.

## Tests

```bash
python -m unittest discover -s tests -t tests
```

10 tests covering keyword/regex matching, anchored regexes, invalid-pattern propagation, case sensitivity, file/stdin scanning, and missing-file behaviour. The suite exits non-zero on any failure — there is no green without execution.

## License

MIT.
