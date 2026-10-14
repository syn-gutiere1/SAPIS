# sap-is-test-hub

**Generic** generator of automated tests for SAP Integration Suite iFlows.
This repository is the "factory": it holds the prompt template, the generation pipeline and the
Groovy harness. The iFlow repositories are the "products" — they hold only the SAP iFlow and the
generated artifacts.

> **Why separate:** the template and the code evolve by PR in a single place, stay versioned and
> auditable, and do not become N diverging copies spread across the iFlow repositories.

---

## Structure

```
.
├── run.py                         # CLI launcher (does not require PYTHONPATH)
├── prompt/
│   └── prompt_template.txt        # generic template sent to Portkey
├── src/pipeline/
│   ├── extract.py                 # extract_from_dir() + scrub() of secrets
│   ├── portkey.py                 # LLM client, prompt, retry/backoff, tokens
│   ├── writer.py                  # safe writing of artifacts (anti path traversal)
│   ├── cli.py                     # CLI with stable exit codes
│   └── legacy_pipeline.py         # original pipeline, historical reference
├── scripts/
│   ├── build_matrix.py            # git diff → changed iFlows
│   └── summarize.py               # table for the Job Summary
├── harness/
│   └── TestHarness.groovy         # runs real .groovy files     [F3]
├── stubs/…                        # stubs of the SAP CPI classes
├── tests/                         # 138 tests
├── legacy/
│   └── requirements-legacy.txt    # deps of the original pipeline (includes Streamlit)
├── .github/workflows/
│   └── reusable-generate.yml      # reusable workflow          [F2]
└── requirements-ci.txt            # CI deps (without Streamlit)
```

---

## How to use the CLI

```bash
# read the artifact and summarize (does not call the LLM)
python run.py --mode extract --source CI-CD/iflows/<iFlowName>

# generate the tests from the iFlow
python run.py --source CI-CD/iflows/<iFlowName> \
              --out    CI-CD/tests/<iFlowName> \
              --plans  CI-CD/test-plans/<iFlowName> \
              --json-report CI-CD/reports/<iFlowName>/manifest.json
```

Exit codes (contract with the workflow):

| Code | Meaning |
|---|---|
| `0` | success |
| `2` | invalid configuration or usage (missing variable, artifact not found) |
| `3` | failure in the call to the LLM gateway |
| `4` | tests failed |

Artifacts written to `--out`: `test_flow.py`, `testdata/**`, `cases.json`, `risks.json`,
`coverage.json` and `raw_llm_output.json`. `cases.json`, `risks.json` and `coverage.json` are copied
too to `--plans`, which is what the approver reads in the PR.

### Optional variables

| Name | Default | Effect |
|---|---|---|
| `LLM_TEMPERATURE` | `0.2` | generation temperature |
| `LLM_MAX_ATTEMPTS` | `3` | total attempts on transient failures |
| `LLM_MAX_FLOW_CHARS` | `120000` | character ceiling of the iFlow XML sent to the model |
| `LLM_MAX_SCRIPTS_CHARS` | `120000` | character ceiling of the scripts JSON |

### About truncation

The original pipeline cut at **30 000 characters, silently**. Measured on a real iFlow
(`SI_Glovia_SalesOrderCreate`, 45 225 characters), that means sending **only two thirds of the
flow** — and the effect was concrete: the truncated run generated **6 scenarios**, against **30** of
the run with the full XML. One third of the iFlow was never analyzed, and nothing indicated that.

The limit is now configurable and **every cut is recorded** in `manifest.json` (`truncated`) and in
the Job Summary, which displays a highlighted warning. The default was raised to 120 000 characters
because the model in use accepts the full input.

**Known limitation:** the cut is still a cut, not *chunking*. For iFlows above the configured limit,
the coverage of the analysis is partial — the warning in the Job Summary is the signal. Splitting the
XML into chunks and merging the resulting JSONs is the next step, not yet implemented; the difficulty
is in merging several `test_flow.py` into a single coherent file.


---


## The prompt template

`prompt/prompt_template.txt` is the **only** template, shared by all iFlow repositories. It is read
by the pipeline through the `AI_PROMPT_FILE` variable.

The template fills two placeholders:

| Placeholder | Content |
|---|---|
| `{{flow_xml}}` | iFlow XML (`.iflw`), already cleaned of secrets by `scrub()` |
| `{{scripts}}` | JSON with the `.groovy` / `.xslt` / `.js` scripts of the artifact |

The model answers with **a single JSON object** containing `scenarios`, `test_data`,
`pytest_script`, `risks` and `coverage_analysis`.

### Rules when editing the template

- Keep the two placeholders — the pipeline warns, but does not fail, if they are missing.
- Every artifact declared in `test_data[].filename` is written to `testdata/`. Prefer paths
  relative to `testdata/` and simple filenames.
- Require **dynamic ports** (ephemeral port / environment variable) in the generated tests: two
  suites with a fixed port collide when they run in parallel.
- Ask for code that **does not depend on an external network** — the tests use local mocks.

---

## Running the real Groovy scripts

`groovy-harness/TestHarness.groovy` runs the iFlow `.groovy` files **for real**, outside the tenant. This
matters because the old pipeline never did that: the `.bat` only compiled the stubs and the generated
tests *simulated* the rule in Python. A defect in the real `.groovy` did not fail the build.

```bash
groovy -cp stubs groovy-harness/TestHarness.groovy \
  --scan-all CI-CD/iflows/<iFlowName>/src/main/resources/script \
  --testdata CI-CD/tests/<iFlowName>/testdata \
  --junit CI-CD/reports/<iFlowName>/junit-groovy.xml
```

**No `groovyc` is required.** Passing the stub sources on the classpath, Groovy compiles on demand.
Besides simplifying CI, this avoids a concrete problem: on Windows `groovyc` is a `.bat`, and
`CreateProcess` does not execute batch files — `subprocess` fails with `FileNotFoundError`.

### Fixture convention

Each script has its own directory inside `--testdata`:

```
testdata/
├── <NomeDoScript>/
│   ├── caso.xml             input payload
│   ├── caso.inject.json     optional: initial headers/properties
│   └── caso.assert.json     optional: checks after the run
└── <caso>.xml               fallback: flat layout, if there is no dedicated directory
```

`assert.json` accepts `headers`, `body_contains` and `log_headers` (what the script would write to the
message log). Without it, the test only confirms that the script runs without an exception.

### Snapshots and the review lock

With `--snapshot-missing`, the harness writes an `assert.json` with the **observed** behavior. This
file is born with the `"__review__"` key and **the case is marked failed while it exists**.

This is deliberate: a snapshot describes what the script *does*, not what it *should do*. Without the
lock, a real defect would be frozen as an expectation and the test would pass forever — that was
exactly what almost happened with the namespace bug described below.

In CI, `--require-fixtures` is used instead of `--snapshot-missing`: a snapshot created on the runner
would never be committed, so the following run would repeat the same warning. With
`--require-fixtures`, the job fails and lists the scripts that do not yet have a reviewed fixture.

### Tenant runtime injection

Real scripts use `messageLogFactory.getMessageLog(message)`, which SAP CPI injects into the binding.
The harness provides `stubs/.../MessageLogFactory.groovy` and `MessageLog.groovy`; without that, any
script that logs fails with `MissingPropertyException`.

The `Message` stub was completed with `getBody(Class)`: the real script calls `getBody(String)`, and
without that overload the harness failed with `MissingMethodException`. The stubs before this change
**never** allowed running Groovy for real.

### Known defect in the Glovia iFlow

`GLOVIA_Conditioncheck_Salesorder_Create.groovy` uses accessors with a namespace prefix:

```groovy
xml.'ns1:notifications'.'ns1:Notification'.'ns1:sObject'.'ns2:giic_OrderTypeCode__c'.text()
```

`XmlSlurper` **discards the prefixes** when exposing the names — `xml.name()` returns `Envelope`, and
not `SOAP:Envelope` — even with `namespaceAware = true`. The correct navigation uses the local names:

```groovy
xml.Body.notifications.Notification.sObject.giic_OrderTypeCode__c.text()
```

Practical consequence: the script marks **every** order as invalid (`OrderCodeValid = false`),
including valid codes such as `YORB`. The harness detects this
(`tests/test_groovy_harness.py::test_script_real_do_glovia_e_reprovado_por_bug_de_namespace`), and the
test must be inverted when the iFlow is fixed.

---

## Templates for iFlow repositories

| Template | What it is for | Prerequisites in the iFlow repo |
|---|---|---|
| [`templates/iFlow-generate-tests.yml`](templates/iFlow-generate-tests.yml) | generation, tests, evidence and PR `sap-is` → `main` | secrets `PORTKEY_AUTH_TOKEN`, `HUB_READ_TOKEN`; variables `PORTKEY_BASE_URL`, `LLM_MODEL`, `LANGUAGE_CODE` |
| [`templates/iFlow-uat-gate.yml`](templates/iFlow-uat-gate.yml) | UAT with human approval and Release of evidence | environment `uat` **created with required reviewers**; environment secrets `UAT_ENDPOINT`, `UAT_CLIENT_ID`, `UAT_CLIENT_SECRET` |

### Known blocker: PR creation by the bot

The `open-pr` job needs `can_approve_pull_request_reviews: true`. Enabling it through the API returns
an **organization policy error**, not a repository one:

```
409 Conflict — The organization does not allow GitHub Actions to create or approve pull requests
```

Even with a token of the correct scope, the enabling must be done **at the organization level**:
`https://github.com/organizations/SAPISRGG/settings/actions` → *Workflow permissions*.
Only after that does the per-repository configuration take effect.

**Alternative in the meantime:** open the PR from the command line with a user PAT
(`gh pr create`), which does not suffer that restriction — that is how PR #1 of the Glovia repository
was created.

### The environment trap

A workflow that references a **nonexistent** environment makes GitHub create it automatically, **with
no protection at all** — and the job runs straight away, without approval. If that happens with
`uat`, UAT starts running on every merge against the real endpoint, with the secrets exposed to code
generated by AI.

**Create the environment with the required reviewers BEFORE enabling the UAT workflow.**

---

## How an iFlow repository consumes this hub

The iFlow repository checks out this hub **pinned to a commit (SHA)** and runs the CLI. The
orchestration YAML lives in the iFlow repository, copied from
[`templates/iFlow-generate-tests.yml`](templates/iFlow-generate-tests.yml).

### Why not a reusable workflow (`workflow_call`)

We tried the "correct" path first: a reusable workflow here, called by the iFlow repository with
`uses: SAPISRGG/sap-is-test-hub/.github/workflows/...@<sha>`. **It does not work with this private
hub.** GitHub does not resolve the reference and the workflow validation fails in 0s with:

```
error parsing called workflow -> "SAPISRGG/sap-is-test-hub/.github/workflows/...@<sha>"
: workflow was not found
```

The file existed and was registered as an active workflow in the hub — I confirmed that through the
API. Testing the same commit with the hub **public**, validation passed and the job ran. That is:
**cross-repository reusable workflow requires the workflow to be accessible to the calling
repository**, and a private repository does not satisfy that in this configuration.

Decision taken: keep the hub private and carry the orchestration YAML to each iFlow repository. The
**logic stays centralized** here (`build_matrix.py`, `run.py`, `summarize.py`, `write_pr_body.py`);
what repeats is only the orchestration.

**Consequence to manage:** when the template is fixed, the iFlow repositories need to be updated. If
that becomes a problem, the ways out are making the hub public (the `HUB_READ_TOKEN` is no longer
needed) or using an organization *workflow template*.

Since this hub is private, the checkout from another repository requires a **fine-grained PAT** with
`Contents: Read-only` on this repository, stored as the `HUB_READ_TOKEN` secret in the iFlow
repository.

### Prerequisites in the iFlow repository

| Item | Where |
|---|---|
| `PORTKEY_AUTH_TOKEN` | Secret |
| `HUB_READ_TOKEN` | Secret (fine-grained PAT, `Contents: read` only on this hub) |
| `PORTKEY_BASE_URL`, `LLM_MODEL`, `LANGUAGE_CODE` | Variables |
| "Allow GitHub Actions to create and approve pull requests" | Settings → Actions → Workflow permissions |

The last item is mandatory: with it off, the `GITHUB_TOKEN` **cannot** create the PR and the
`open-pr` job fails with `Resource not accessible by integration`.


---

## Variables and secrets

| Name | Where | Note |
|---|---|---|
| `PORTKEY_BASE_URL` | Variable | URL of the Portkey gateway |
| `PORTKEY_AUTH_TOKEN` | **Secret** | never commit |
| `LLM_MODEL` | Variable | model exposed by the virtual key |
| `AI_PROMPT_FILE` | Variable | `prompt/prompt_template.txt` |

The iFlow repository defines which version of the hub to use; Portkey credentials are passed to the
reusable workflow via `secrets: inherit`.

---

## Implementation status

| Phase | Scope | Status |
|---|---|---|
| F0 | hygiene (`.gitignore`, `.env.example`) | ✅ in this repository |
| F1a | `extract_from_dir()`, matrix by `git diff`, `requirements-ci.txt` | ✅ implemented and tested |
| F1b | Portkey client, writing of the artifacts, CLI with exit codes, `summarize.py` | ✅ implemented and tested |
| F2 | reusable generation workflow + evidence publication | ⏳ to implement |
| F3 | real Groovy harness (`MessageLogFactory` mocked) | ⏳ to implement |
| F4 | UAT gate with required reviewers | ⏳ to implement |
| F5 | measured coverage (`pytest-cov`), structured output in Portkey | ⏳ to implement |

Already resolved ahead of time, although planned for F5: sanitization of filenames
(path traversal), cleanup of the output directory, retry/backoff on the gateway, token logging and a
provenance manifest.

### How to run the tests

```bash
pip install -r requirements-ci.txt
pytest
```

| File | Covers |
|---|---|
| `tests/test_extract.py` | extractor by directory, including the real package in `export/Order` |
| `tests/test_build_matrix.py` | matrix by diff, against a real Git repository with 6 commits |
| `tests/test_writer.py` | path traversal, extension whitelist, output cleanup |
| `tests/test_portkey.py` | retry/backoff, truncation, JSON extraction, tokens |
| `tests/test_cli.py` | exit codes and secret scrubbing end to end |
| `tests/test_summarize.py` | Job Summary with and without manifest/junit |

> **Note about `conftest.py`:** it replaces the `tmp_path` and `tmp_path_factory` fixtures. The
> native `tmp_path_factory` collects the previous numbered directories and fails with
> `PermissionError` in environments that do not allow removing temporaries.


