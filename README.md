# SAPIS

Test generation for SAP Integration Suite iFlows. The pipeline reads an iFlow
artifact (`.iflw` plus its `.groovy` / `.xslt` / `.js` scripts), asks an LLM for a test
plan and a pytest suite, checks the result mechanically, and runs the real Groovy
scripts against reviewed fixtures.

One repository, public, no organisation. The iFlows, the pipeline, the harness and
the stubs all live here, so both workflows are self-contained: there is no hub, no
`HUB_READ_TOKEN`, no cross-repository checkout and no `workflow_call`.

The model generates a plan that a human reviews in a pull request; it never gets
access to credentials before that review.

---

## Layout

```
.
|-- run.py                          # CLI entry point (no PYTHONPATH required)
|-- conftest.py                     # puts src/ on sys.path; overrides tmp_path
|-- pyproject.toml                  # pythonpath = src, testpaths = tests
|-- requirements-ci.txt             # CI dependencies (no Streamlit)
|-- .env.example                    # local variables (never a real secret)
|-- prompt/
|   `-- prompt_template.txt         # the only prompt, sent through Portkey
|-- src/pipeline/
|   |-- extract.py                  # artifact discovery + scrub() of secrets
|   |-- portkey.py                  # LLM client, prompt, retry/backoff, tokens
|   |-- writer.py                   # safe writing of artifacts (anti path traversal)
|   |-- cli.py                      # CLI with stable exit codes
|   `-- __main__.py                 # `python -m pipeline`
|-- scripts/
|   |-- build_matrix.py             # git diff -> changed iFlows + has_iflow_change
|   |-- check_generated_tests.py    # static checks on the generated suite
|   |-- summarize.py                # Job Summary of the test workflow
|   |-- summarize_uat.py            # Job Summary and Release notes of the UAT gate
|   `-- write_pr_body.py            # PR body built from the evidence
|-- groovy-harness/
|   `-- TestHarness.groovy          # runs the REAL .groovy scripts
|-- stubs/                          # SAP CPI classes (Message, MessageLog, ...)
|-- tests/                          # the pipeline's own pytest suite (208 tests)
|-- legacy/                         # original pipeline, historical reference
|-- docs/, templates/               # empty directories, not tracked by Git
`-- .github/workflows/
    |-- generate-tests.yml          # sap-is -> tests -> PR into main
    `-- uat-gate.yml                # main -> UAT behind a human approval gate
```

### The CI-CD tree

Everything test-related is under `CI-CD/`, keyed by iFlow name. `CI-CD/iflows/` is the
source of truth (the package SAP writes through `Git Push`); the other three trees are
keyed the same way and produced by the pipeline. `CI-CD/test-plans/` and
`CI-CD/reports/` are not in Git until the first run creates them.

```
CI-CD/
|-- iflows/
|   `-- <iFlowName>/                     # SAP IS package, as pushed by the tenant
|       |-- metainfo.prop
|       |-- META-INF/MANIFEST.MF
|       `-- src/main/resources/
|           |-- scenarioflows/integrationflow/<iFlowName>.iflw
|           |-- script/*.groovy | *.gsh
|           |-- mapping/*.mmap
|           |-- wsdl/*.wsdl
|           `-- parameters.prop
|-- tests/
|   `-- <iFlowName>/
|       |-- test_flow.py                 # generated pytest suite
|       `-- testdata/
|           |-- <fixture>.xml            # generated fixtures
|           `-- groovy/                  # hand-written fixtures for the harness
|               |-- README.md
|               `-- <ScriptName>/
|                   |-- <case>.xml
|                   |-- <case>.inject.json
|                   `-- <case>.assert.json
|-- test-plans/
|   `-- <iFlowName>/                     # cases.json, risks.json, coverage.json
`-- reports/
    `-- <iFlowName>/                     # manifest.json, junit.xml, report.html, logs
```

Until a suite has been generated, `CI-CD/tests/<iFlowName>/` holds only the
hand-written `testdata/groovy/` fixtures; `CI-CD/test-plans/` and `CI-CD/reports/`
appear on the first run.

---

## How to run

### The CLI

`run.py` is the entry point. It adjusts `sys.path` before importing, so it works
without exporting `PYTHONPATH`. `python -m pipeline` is equivalent and needs
`PYTHONPATH=src`.

There are two modes: `extract` reads the artifact and prints a diagnostic summary
without calling the model; `generate` reads, calls the model and writes the artifacts.
`generate` is the default.

```bash
# read the artifact and summarize (does not call the LLM)
python run.py --mode extract --source CI-CD/iflows/<iFlowName>

# generate the suite, the plan and the provenance manifest
python run.py --source CI-CD/iflows/<iFlowName> \
              --out    CI-CD/tests/<iFlowName> \
              --plans  CI-CD/test-plans/<iFlowName> \
              --json-report CI-CD/reports/<iFlowName>/manifest.json
```

| Flag | Default | Meaning |
|---|---|---|
| `--mode {extract,generate}` | `generate` | `extract` only reads and summarizes |
| `--source` | required | artifact directory, `.iflw` file or `.zip` package |
| `--out` | `tests` | destination of `test_flow.py` and `testdata/` |
| `--plans` | `--out` | destination of `cases.json`, `risks.json`, `coverage.json` |
| `--json-report` | none | path of `manifest.json` |
| `--env-file` | none | local `.env`, loaded without overriding the environment |

`--source` is layout-agnostic: the extractor finds any `.iflw` under the given root
(falling back to an `.xml` whose path contains `integrationflow`) and collects the
sibling scripts by walking the tree, stripping the `src/main/resources/` prefix from
the keys. It never assumes fixed directory names.

### Exit codes

The exit codes are the contract with the workflow: a configuration error must not look
like a test failure, and a gateway failure must not look like success.

| Code | Meaning | Returned by |
|---|---|---|
| `0` | success | both modes |
| `2` | invalid configuration or usage: missing `PORTKEY_AUTH_TOKEN` or `LLM_MODEL`, artifact not found, unreadable output directory, no `test_flow.py` produced... | both modes |
| `3` | failure in the call to the LLM gateway, invalid JSON from the model, or a response without `test_flow.py` | `generate` |
| `4` | tests failed | reserved, currently unreachable |

`EXIT_TESTS = 4` is declared in `src/pipeline/cli.py` as part of the contract, but no
code path returns it: the CLI never runs the suite. The workflow runs pytest in its own
step and applies the verdict there.

### Artifacts written to `--out`

`test_flow.py`, `testdata/**`, `cases.json`, `risks.json`, `coverage.json` and
`raw_llm_output.json`. `cases.json`, `risks.json` and `coverage.json` are also copied to
`--plans`, which is what the approver reads in the PR.

`manifest.json` records the provenance: the iFlow name and SHA-256, the individual hash
of every script, the model, base URL, temperature, `prompt_sha256`, token counts,
attempts, duration, the `truncated` list and the GitHub run id / SHA / ref. It is the
link between a test file, a commit and the prompt that produced it.

### Environment

| Name | Where | Note |
|---|---|---|
| `PORTKEY_AUTH_TOKEN` | secret | never committed; the run fails with exit 2 without it |
| `LLM_MODEL` | variable | the model exposed by the virtual key |
| `PORTKEY_BASE_URL` | variable | defaults to `https://api.portkey.ai/v1`; `/v1` is appended when missing |
| `LANGUAGE_CODE` | variable | language of the generated prose; defaults to `en_US` |
| `AI_PROMPT_FILE` | variable | `prompt/prompt_template.txt` |
| `LLM_TEMPERATURE` | optional | `0.2` |
| `LLM_MAX_ATTEMPTS` | optional | `3` (1 attempt + retries) |
| `LLM_MAX_FLOW_CHARS` | optional | `120000` |
| `LLM_MAX_SCRIPTS_CHARS` | optional | `120000` |

For local use, copy `.env.example` and pass it with `--env-file`. CI injects everything
through the workflow environment and has no `.env`; variables already defined take
precedence over the file, so a file never replaces a real secret. The `SAP_IS_*` entries
in `.env.example` belong to the legacy REST extraction in `legacy/` and are not part of
the CI flow.

### Running the tests locally

```bash
pip install -r requirements-ci.txt
pytest
```

`pyproject.toml` sets `pythonpath = ["src"]` and `testpaths = ["tests"]`; `conftest.py`
puts `src/` on `sys.path` before any import, so `pytest` from the repository root works
without further setup.

**Current count: 208 tests**, all passing. Verified with `python -m pytest -q` in this
checkout, with Groovy on `PATH`.

`conftest.py` replaces the `tmp_path` and `tmp_path_factory` fixtures. The native
`tmp_path_factory` garbage-collects the previous numbered directories
(`pytest-of-<user>`); in environments that do not allow removing temporaries that
collection fails with `PermissionError` and takes down the setup of every test that uses
the fixture. The replacement creates a unique directory under the system temp root and
never deletes anything, with the same semantics: a new and empty `Path` per test.

`tests/test_groovy_harness.py` needs `groovy` on `PATH`. When it is missing, the whole
module is skipped with an explicit reason instead of failing.

---

## How the pipeline works

1. **Extract** (`extract.py`). Locate the `.iflw` and the scripts under `--source`.
   Excluded directories (`.git`, `tests`, `test-plans`, `reports`, `legacy`, `stubs`,
   ...) are compared against the path relative to the given root, never the absolute
   path, so a root whose own name matches an excluded directory does not exclude itself.
   Scripts larger than 512 KiB are truncated with a warning.
2. **Scrub** (`extract.py`). Before anything leaves the machine, credentials are
   redacted: `password`, `client_secret`, `privatekey`, `secret`, `<password>`,
   `<clientSecret>`, `<user>`, `oauth_token`, `access_token`, `api_key` and
   `Basic <base64>`. The `.iflw` is treated as untrusted input.
3. **Assemble the prompt** (`portkey.py`). `{{flow_xml}}` and `{{scripts}}` are filled
   with `str.replace()`, not `str.format()`, so the JSON braces in the template do not
   break it.
4. **Call the model** (`portkey.py`). The `openai` SDK pointed at Portkey, retried with
   exponential backoff and jitter on transient failures only (rate limit, 429, timeout,
   connection, 502/503/504, overloaded). A permanent error is not retried. Code fences
   are stripped and the JSON is validated, with the start of the response quoted when
   parsing fails.
5. **Write** (`writer.py`). Every filename coming from the model goes through
   `safe_relative_path()`: only the base name survives, so `../` and absolute paths
   collapse; empty, `.`, `..`, device names and pipeline-reserved names are rejected, and
   fixtures must use a whitelisted extension. A second `_ensure_within()` check confirms
   the resolved path stays inside the destination. The destination is cleaned before
   every run, because leftovers from a previous run change the test count without the
   code having changed.
6. **Record provenance** (`cli.py`). The manifest from step 5 plus the hashes, the token
   usage and the `truncated` list.

### Generation is gated on the `.iflw`

The model runs with `temperature > 0` and **no seed**, so it is not deterministic.
Regenerating from an unchanged `.iflw` would produce a different `test_flow.py` for the
same input: a diff with no real change, which makes the PR unreviewable and wastes
quota. `build_matrix.py` therefore exposes `has_iflow_change` per artifact and the
`generate` job only regenerates when that flag is set. A change in a `.groovy` alone
still runs the already-versioned suite, and the Groovy harness runs the real script.

A run that did not generate still has to have a suite to run: a separate step requires
`test_flow.py` to exist, so a job cannot report success having tested nothing.

### Truncation is recorded, never silent

The original pipeline cut the XML at a fixed **30 000 characters, silently**. That limit
was measured against a real flow, `SI_Glovia_SalesOrderCreate`, which is **45 225
characters** -- so a third of the flow was dropped without a trace, and the effect was
concrete: the truncated run produced **6 scenarios** where the full XML produced **30**.

The limit is now configurable through `LLM_MAX_FLOW_CHARS` (and `LLM_MAX_SCRIPTS_CHARS`
for the scripts JSON). Every cut is returned by `build_prompt()` in the `truncated` list,
written to `manifest.json` as `truncated`, and surfaced in the Job Summary as a
highlighted warning. The default was raised to 120 000 characters because the model in
use accepts the full input; an invalid value falls back to the default instead of
failing.

**Known limitation:** the cut is still a cut, not chunking. For an iFlow above the
configured limit the analysis is partial, and the warning in the Job Summary is the only
signal. Splitting the XML and merging the resulting JSONs is not implemented; the
difficulty is merging several `test_flow.py` into one coherent file.

### The prompt

`prompt/prompt_template.txt` is the only template. It fills two placeholders --
`{{flow_xml}}` and `{{scripts}}` -- and asks for a single JSON object with `scenarios`,
`test_data`, `pytest_script`, `risks` and `coverage_analysis`. The system prompt
additionally instructs the model to answer in the language given by `LANGUAGE_CODE`.

The template carries the hard rules for the generated suite: never perform real network
I/O, pick one mocking strategy and apply it consistently, use an ephemeral port for any
local mock server, never hardcode an internal hostname, and do not write a Python
re-implementation of a Groovy script as a substitute for exercising it.

Because the model does not always obey, the writer reads the response tolerantly
(`find_key()` accepts key-name variations, a single `test_data` object is normalized to a
list, a `pytest_script` given as a list is concatenated) and rejects what it cannot
safely write. `find_key()` is explicitly a heuristic, to be replaced by structured
output.

---

## The Groovy harness

`groovy-harness/TestHarness.groovy` runs the iFlow `.groovy` files **for real**, outside
the tenant. This is the point of the harness: the previous pipeline never executed them.
The old `.bat` only compiled the stubs and the generated tests *simulated* the rule in
Python, so a defect in the real script never failed the build -- the test passed while
validating a Python copy of the rule.

```bash
groovy -cp stubs groovy-harness/TestHarness.groovy \
  --scan-all CI-CD/iflows/<iFlowName>/src/main/resources/script \
  --testdata CI-CD/tests/<iFlowName>/testdata \
  --junit CI-CD/reports/<iFlowName>/junit-groovy.xml
```

| Flag | Meaning |
|---|---|
| `--script <file.groovy>` | run one script |
| `--scan-all <dir>` | run every `.groovy` in a directory (one JUnit `testsuite` each) |
| `--testdata <dir>` | fixtures root (required) |
| `--junit <file>` | write a JUnit report for `dorny/test-reporter` |
| `--filter <substring>` | run only the fixtures whose name contains the substring |
| `--snapshot-missing` | write an assertion file from the observed behaviour |
| `--require-fixtures` | fail when a script has no reviewed fixture |

Exit codes: `0` all passed, `1` some failure or a pending fixture, `2` invalid usage.

**No `groovyc` step is required.** Passing the stub *sources* on the classpath makes
Groovy compile them and the script on demand. Besides simplifying CI, this avoids a real
Windows trap: `groovyc` is a `.bat`, and `CreateProcess` cannot execute batch files, so
`subprocess` fails with `FileNotFoundError`.

### Fixture convention

One folder per script, named exactly like the script without the `.groovy` extension. If
the folder does not exist, the harness falls back to the flat `--testdata` directory; if
that has no payload either, the case is skipped with a warning (or fails, under
`--require-fixtures`).

```
testdata/
|-- <ScriptName>/
|   |-- <case>.xml              input payload, read as the message body (required)
|   |-- <case>.inject.json      optional: {"headers": {...}, "properties": {...}}
|   `-- <case>.assert.json      optional: {"headers": {...}, "body_contains": [...],
|                                           "log_headers": {...}}
`-- <case>.xml                  fallback: flat layout, when there is no dedicated folder
```

Payloads are every file that is not an `.assert.json` or an `.inject.json`. Injection is
applied to the message before the script runs; assertions are checked after. Without an
`.assert.json` the case only proves that the script runs without throwing.

`log_headers` asserts on what the script wrote to the message log through
`messageLogFactory.getMessageLog(message)`. The tenant runtime injects that factory into
the script binding; the harness provides it as a stub, and without it any logging script
fails with `MissingPropertyException`. The `Message` stub also implements
`getBody(Class)`, because real scripts call `getBody(String)` and the earlier stub caused
a `MissingMethodException`.

### Snapshots and the review lock

With `--snapshot-missing`, the harness writes a `<case>.assert.json` from the **observed**
behaviour. That file is born with a `"__review__"` key, and while the key exists the case
is marked **FAILED**:

```
snapshot awaiting human review: confirm the observed values and remove the
"__review__" key from the .assert.json file
```

This is deliberate. A snapshot describes what the script *does*, not what it *should do*.
Without the lock, a real defect would be frozen as an expectation and the test would pass
forever -- which is very close to what happened with the namespace defect below.

CI uses `--require-fixtures` instead. A snapshot created on the runner would never be
committed, so the next run would repeat the same warning; with `--require-fixtures` the
job fails and lists the scripts that still have no reviewed fixture.

The fixtures in `CI-CD/tests/SI_Glovia_SalesOrderCreate/testdata/groovy/` are hand-written
and describe the behaviour the flow is *supposed* to have. They need no change when the
script is fixed: the job turns green on its own. See
[the fixtures README](CI-CD/tests/SI_Glovia_SalesOrderCreate/testdata/groovy/README.md)
for the namespace defect in detail.

---

## Static checks on the generated suite

`scripts/check_generated_tests.py` is a mechanical check on `test_flow.py`, because **a
prompt is an instruction, not a guarantee**. In a real run the model produced a test that
decorated `@patch('requests.post')` and then called `requests.Session().post(...)`. The
patch does not intercept that path, and the test tried to resolve `cpi-dev.company.corp`
-- an internal hostname that existed neither in the artifact nor in the prompt. It was
invented.

The risk is not the DNS failing on the runner: it is the name *resolving*. A runner on
the internet would then make a real request to an internal host.

The checker reports three categories and exits `0` when clean, `1` on findings, `2` on
invalid usage:

1. a network call with no mock anywhere in the file (`requests.*`, `requests.Session`,
   `urlopen`);
2. an internal or invented host that is neither an XML namespace URI nor a reserved test
   host -- including the suffix list configured through `FORBIDDEN_HOST_SUFFIXES`, since
   the client-specific list must not be hardcoded;
3. a fixed port in a local mock server, which collides between suites running in
   parallel.

Documentation hosts, loopback addresses and namespace URIs are not findings. A legitimate
host can be allowed with `--allow`; `--quiet` suppresses the trailing explanation.
The workflow runs the checker without either flag, so any finding fails the job.

---

## Workflows

### `generate-tests.yml` -- Generate tests

Triggers on a push to `sap-is` and on manual dispatch (`full_scan` ignores the diff and
regenerates every tracked iFlow). `sap-is` is the entry point of the native `Git Push`
of SAP Integration Suite; `main` is the protected branch and receives changes only
through the PR this workflow opens.

| Job | What it does |
|---|---|
| `discover` | `build_matrix.py --before/--after` builds the matrix; writes it to the Job Summary |
| `generate` | one job per changed iFlow: generate when `has_iflow_change`, require `test_flow.py`, run `check_generated_tests.py`, run pytest, install the pinned Groovy 4.0.24 and run the harness when a `.groovy` changed, publish both reports, write the summary, upload the evidence, then apply the verdict |
| `open-pr` | builds the PR body from the evidence and creates or updates the PR into `main` |

Notes: the matrix has `max-parallel: 3` and `fail-fast: false`, so one failing iFlow does
not cancel the others; `concurrency` supersedes an older run on the same ref; `pytest` and
the harness run with `continue-on-error: true` and the verdict is applied in the last two
steps, so the reports and the evidence survive a failure. The two `dorny/test-reporter`
steps must use distinct `name:` values: the reporter keys the Check Run by that name, and
two reports with the same name overwrite each other in the Checks tab.

### `uat-gate.yml` -- UAT (requires approval)

Triggers on a push to `main` and on manual dispatch. The gate is `environment: uat` with
required reviewers: the job **pauses before any step** and the environment secrets are
released only after somebody approves. The secrets live on the environment, not on the
repository, precisely so that a job that did not pass the gate cannot read them at all.
`concurrency` is serialised (`cancel-in-progress: false`) because testing two versions at
the same time against one endpoint produces evidence that cannot be attributed to a
commit. Evidence is uploaded as an artifact and, only on success, attached to a Release
tagged `uat-<run_id>`, because the artifact expires in 30 days and UAT evidence has to
last.

### The two operational prerequisites

Both are real, and both are still yours to configure in the repository settings.

**1. Allow GitHub Actions to create and approve pull requests.**
Settings > Actions > General > Workflow permissions: select *Read and write permissions*
and tick *Allow GitHub Actions to create and approve pull requests*. Without the second
box the `open-pr` job cannot create the PR: `gh pr create` fails even though
`permissions: pull-requests: write` is granted, and the job detects that and fails with
the path to the setting. On a user account this setting is per repository, not per
organisation.

**2. Create the `uat` environment with required reviewers *before* enabling the UAT
workflow.** Settings > Environments > New environment, name `uat`, then add required
reviewers, restrict *Deployment branches* to `main`, and add the environment secrets
`UAT_ENDPOINT`, `UAT_CLIENT_ID`, `UAT_CLIENT_SECRET`.

The order matters, and this is a trap rather than a formality: a workflow that references
a **nonexistent** environment makes GitHub **create it automatically, with no protection
at all** -- and then the job runs straight through with no approval. If that happens with
`uat`, UAT runs on every merge into `main` against the real endpoint, with the credentials
exposed to AI-generated code.

---

## Known findings

- **Namespace defect in the Glovia condition script.**
  `GLOVIA_Conditioncheck_Salesorder_Create.groovy` navigates with namespace-prefixed
  accessors (`xml.'ns1:notifications'.'ns1:Notification'....`). `XmlSlurper` discards the
  prefixes when exposing element names -- `xml.name()` returns `Envelope`, not
  `SOAP:Envelope` -- even with `namespaceAware = true`. Those accessors return nothing, the
  extracted order code is `""`, and the valid-code list never contains `""`. Consequence:
  the script marks **every** order as invalid (`OrderCodeValid = false`), including valid
  codes such as `YORB`. The correct navigation uses the local names
  (`xml.Body.notifications.Notification.sObject.giic_OrderTypeCode__c.text()`). The
  harness detects it, and the tests that pin it are
  `tests/test_groovy_harness.py::test_harness_detects_script_with_namespace_bug` and
  `tests/test_groovy_harness.py::test_scan_all_on_the_real_artifact_reports_the_defect`;
  the latter asserts the defect is still present, so it will start failing when the iFlow
  is fixed. That failure is the signal to invert the reviewed fixtures. See
  [the fixtures README](CI-CD/tests/SI_Glovia_SalesOrderCreate/testdata/groovy/README.md).

- **Truncation dropped a third of the flow, silently.** A fixed 30 000-character limit
  against a 45 225-character real flow; the truncated run produced 6 scenarios instead of
  30, and nothing indicated that a third of the iFlow was never analyzed. Now
  configurable, and every cut is recorded in `manifest.json` and surfaced in the Job
  Summary. Chunking is still not implemented -- above the configured limit the analysis is
  partial.

- **The generated tests are not deterministic.** The call uses `temperature` (`0.2` by
  default) with no seed, so the same input can produce a different `test_flow.py`. This is
  why generation is gated on the `.iflw`: regenerating without a flow change churns the
  diff and wastes quota. It also means a generated suite is only meaningful together with
  the manifest that records the prompt, the model and the artifact hashes.

- **The prompt is not enforced.** The model has been observed producing a mock that does
  not intercept the path the code under test actually uses, and inventing an internal
  hostname absent from both the artifact and the prompt. The static checker catches these
  classes of defect, but it is a pattern check, not a proof.

- **Language.** Everything in this repository is in English: code, comments, docstrings,
  messages, tests, workflows, prompt and documentation. Two Portuguese artifacts are kept
  **deliberately**, and neither is an oversight:

  - The legacy risk keys `risco` / `mitigacao` are still accepted by
    `scripts/write_pr_body.py` alongside `risk` / `mitigation`. The model writes the
    response in the language configured by `LANGUAGE_CODE`, so a deployment using
    `LANGUAGE_CODE=pt-BR` legitimately produces Portuguese keys. Removing the fallback
    would break PR bodies for those deployments.
  - `LANGUAGE_CODE=pt-BR` is the value shipped in `.env.example`. That is configuration,
    not prose: it selects the language of the generated artifacts. Change it to `en_US`
    if the team prefers English output.

  `--has-tests`, `--approver`, `--allow` and `--quiet` were renamed to English once it
  became clear they were worth the churn; the workflow and the tests were updated in the
  same commit.

- **`EXIT_TESTS = 4` is declared but never returned.** The CLI documents "tests failed" as
  part of its contract, but it never runs the suite; pytest runs in a workflow step and
  the verdict is applied there.


---

## Legacy

`legacy/pipeline_original.py` and `legacy/requirements-legacy.txt` keep the original
pipeline for reference, including its Streamlit dependency. Streamlit is deliberately
absent from `requirements-ci.txt`, which is what the runner installs. The `SAP_IS_*`
variables in `.env.example` belong to the old REST extraction and are not part of the CI
flow: the iFlow now arrives through the tenant's native `Git Push`, already exploded.
