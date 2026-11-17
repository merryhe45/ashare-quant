# M4A: artifact-only local MCP server

Implementation: `src/ashare_research/mcp_readonly/`. This path is independent of
the production API, lake reader, Azure, and experiment execution modules. It
uses the installed MCP Python SDK's low-level Server registration and Pydantic
models, rather than handwritten JSON-RPC. SDK registration permits strict,
sanitized input validation without helper-level implicit argument coercion.

**No server has been started and no Codex configuration has been changed.**
Connecting or launching for a live test remains pending explicit approval.

## Dependencies and unit tests

Verified interpreter:

`C:/Users/merry/AppData/Local/Programs/Python/Python314/python.exe`

Installed versions: MCP 1.29.1 and Pydantic 2.13.4. Recorded separately in
`configs/mcp-readonly-requirements.txt`; no package installation is needed or
authorized. The existing project environment is not assumed to contain the SDK.

Unit tests, from the repository root:

```powershell
$env:PYTHONPATH = "$PWD/research/src"
& 'C:/Users/merry/AppData/Local/Programs/Python/Python314/python.exe' -B -m unittest discover -s research/tests -v
```

These tests call service handlers and the SDK request dispatcher in memory.
They do not run a transport or start the stdio server. Temporary malformed
fixtures are created and removed only under research/artifacts; the service
itself never writes, and existing artifacts are hash-checked before/after tests.

## Future Codex in VS Code connection

After separate approval, add the following manually to Codex's user or trusted
project `config.toml`. Do not execute the launch command as part of unit testing.

```toml
[mcp_servers.ashare_research_readonly]
command = 'C:/Users/merry/AppData/Local/Programs/Python/Python314/python.exe'
args = ['-B', '-m', 'ashare_research.mcp_readonly.server']
cwd = 'C:/Users/merry/Projects/ashare-quant'
enabled_tools = ['list_experiments', 'get_experiment', 'get_factor_results', 'get_research_report']

[mcp_servers.ashare_research_readonly.env]
PYTHONPATH = 'C:/Users/merry/Projects/ashare-quant/research/src'
PYTHONDONTWRITEBYTECODE = '1'
PYTHONUTF8 = '1'
PYTHONIOENCODING = 'utf-8'
```

Then restart the Codex extension and inspect its MCP tools. This is Codex MCP
configuration, not VS Code Copilot's separate MCP configuration. Paths above
are for the verified native Windows host; WSL would need its own interpreter
and filesystem paths. No configuration mutation is automated here.

Official OpenAI documentation for stdio and Codex IDE configuration:
https://learn.chatgpt.com/docs/extend/mcp

The launch command is fixed host configuration. No MCP request accepts commands,
shell arguments, root directories, filenames, Python expressions, SQL or URLs.

## Tool inputs

Every tool exposes a strict Pydantic JSON input schema: extra properties and
type coercions (including string-to-integer conversion) are rejected. Inputs
are flat objects, not nested under a `request` property.

| Tool | Inputs |
|---|---|
| list_experiments | stage: M2/M3A/M3B or null; offset=0; limit=20 (max 50) |
| get_experiment | experiment_id; section: summary/coverage/definitions/provenance/artifacts; offset; limit |
| get_factor_results | experiment_id; factor_ids (1–6 predefined IDs, default momentum_60); scope: all/native/matched/legacy_label_first; view: summary/daily; optional ISO start_date/end_date for daily only; offset; limit |
| get_research_report | experiment_id; offset=0; max_characters=8000 (max 20000) |

Examples of tool arguments (not shell commands):

```json
{"experiment_id":"m3b-20261102T084617369458Z-f97967d4","factor_ids":["momentum_60"],"scope":"matched"}
```

```json
{"experiment_id":"m3b-20261102T084617369458Z-f97967d4","view":"daily","scope":"native","start_date":"2020-01-02","end_date":"2020-01-31","limit":10}
```

Responses have api_version, ok, data, source, research_context, pagination and
error fields. SDK responses include structuredContent and JSON text content;
errors set isError=true and a sanitized stable code. Codes include
INVALID_ARGUMENT, NOT_FOUND, INVALID_ARTIFACT, UNSUPPORTED_SCHEMA,
UNAVAILABLE_FACTOR, UNAVAILABLE_SCOPE, ARTIFACT_CHANGED, RESULT_TOO_LARGE,
and INTERNAL_ERROR. No tracebacks or external file paths are returned.

Daily pagination flattens factor/scope/date rows in date/factor/scope order.
Summary pagination pages factor/scope combinations. Date filters select saved
daily rows, never recompute aggregates. Pagination has offset, limit, total and
next_offset. Report offsets count decoded UTF-8 characters and preserve CRLF.
No report text is executed, rendered as active HTML, or treated as instructions.

## Schema normalization and interpretation

- M2 schema 1.0: momentum_60 with unspecified definition version and legacy
  label-first semantics. Missing-outcome statistics and spread are unavailable
  where not saved; they are null/explicitly unavailable, never invented.
- M3A schema 2.0: momentum_60, native signal-first evaluation; saved definition
  parameters and policy are returned without inventing a factor version.
- M3B schema 3.0: all defined factors, native and matched results, saved factor
  definition versions, disabled-liquidity reasons and coverage diagnostics.
- Matched readiness is determined before labels; native readiness varies by
  factor. Comparison rows using cross-factor common dates are returned
  separately from each factor's own accepted-date bucket means.
- Rank IC is rank association. High-minus-low is a descriptive outcome spread.
  Forward close-to-close labels are not executable returns or portfolio profit.
- Missing outcomes remain unknown, not zero. Numerical correctness is not
  investment profitability. Fixed exploratory cohorts do not represent the full
  market or remove survivorship bias. M1 limitations accompany retrievals.

No factors, returns, aggregates or new research interpretations are calculated.
The AI agent compares retrieved evidence with its periods, samples and caveats.

## Limits and security boundaries

- Application data root is fixed at research/artifacts; only direct experiment
  IDs are accepted. Default root is derived from server code, never requests.
  Test-only constructors can use trusted fixture directories.
- File allowlist consists of results.json, report.md and known research Parquet
  names. Parquet is only fingerprinted for inventory, never parsed or queried.
  Unsupported files are ignored in inventories and cannot be requested.
- IDs reject traversal, separators, drive/UNC paths, colons/alternate streams,
  dots, trailing whitespace and control characters. Every ancestor is checked
  for symlinks and Windows reparse points (including junctions). Hard-linked,
  non-regular and oversized files are rejected. Windows opened handles are
  checked against their actual final paths before contents are read.
- JSON maximum 16 MiB, report maximum 1 MiB, nesting depth maximum 32,
  normalized JSON traversal maximum one million nodes. Duplicate keys and
  nonfinite numbers are rejected. Discovery/inventory directory count cap 256.
- Structured response budget is conservatively 48 KiB, keeping duplicated MCP
  text/structured wire responses under 128 KiB. A wire-size check also applies.
  Reduce page size if RESULT_TOO_LARGE is returned; no silent truncation.
- Reads verify file identity, timestamps and size across opening and completion.
  Hashes refer to bytes actually parsed. They establish content identity, not
  authenticity, trusted provenance or an atomic multi-file snapshot.
- Recorded source/input hashes are returned as historical labels only. The
  server never dereferences them into the Data Lake, source files or network.
  Absolute external references and data-root configuration are omitted.
- No persistent caches, logs, artifact writes, subprocesses or network calls are
  used by request handlers. stdout is protocol-only; SDK diagnostics use stderr.
  Use -B at launch to prevent interpreter bytecode writes during imports.
- MCP tool annotations declare read-only, idempotent, non-destructive and
  closed-world behavior; annotations are not an OS security sandbox. Protect
  server code and artifacts with normal local permissions. A hostile process
  able to mutate files concurrently still defeats authenticity guarantees.
- No authentication is added to stdio: access is through the local host process.
  Results returned to Codex become model context; local artifact access does not
  imply that the model itself runs offline. Host workspace policies still apply.

All legacy research code, production files and saved artifacts are preserved.
Live protocol negotiation, Codex discovery and integration behavior remain
unverified until a separately approved launch test.
