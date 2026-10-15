# Groovy test fixtures — SI_Glovia_SalesOrderCreate

Fixtures for `groovy-harness/TestHarness.groovy`, which executes the **real** `.groovy`
scripts of this iFlow outside the SAP tenant.

## Layout

One folder per script, named exactly like the script without the `.groovy` extension:

```
GLOVIA_Conditioncheck_Salesorder_Create/
├── valid_order_yorb.xml             payload with a recognised code (YORB)
├── valid_order_yorb.assert.json     expects OrderCodeValid = true
├── invalid_order_code.xml           payload with an unrecognised code (XYZ9)
└── invalid_order_code.assert.json   expects OrderCodeValid = false

Interface_Generic_Headers/
├── payload.xml                      body is irrelevant to this script
├── payload.inject.json              headers set on the message before execution
└── payload.assert.json              expects those headers to be written to the log
```

If a script has no dedicated folder, the harness falls back to the flat
`testdata/` directory. In CI the harness runs with `--require-fixtures`, so a
script without a reviewed fixture fails the job and names it.

## File conventions

| Suffix | Purpose |
|---|---|
| `<case>.xml` / `<case>.json` | input payload, read as the message body |
| `<case>.inject.json` | optional: `{"headers": {...}, "properties": {...}}` applied before execution |
| `<case>.assert.json` | optional: `{"headers": {...}, "body_contains": [...], "log_headers": {...}}` checked after execution |

Without an `.assert.json` the test only proves the script runs without throwing.

## The two defects these fixtures exposed, and their fix

The fixtures were written against the **correct** behaviour (`valid_order_yorb`
expects `OrderCodeValid = true`), so they stayed red until the script was fixed.
They exposed two defects, and both are now corrected in the artifact:

**1. The script did not compile on Groovy 4.** `new XmlSlurper()` was called with no
import. On Groovy 3 it resolved through the implicit `groovy.util` import; on
Groovy 4 that import is gone and the whole script raised
(`unable to resolve class XmlSlurper`). SAP documents the same fix for upgrade
readiness. The script now imports `groovy.xml.XmlSlurper` explicitly.

**2. Navigation by namespace prefix never matched.** The script read:

```groovy
xml.'ns1:notifications'.'ns1:Notification'.'ns1:sObject'.'ns2:giic_OrderTypeCode__c'.text()
```

`XmlSlurper` **discards namespace prefixes** when exposing element names —
`xml.name()` returns `Envelope`, not `SOAP:Envelope` — and that holds even with
`namespaceAware = true`. Those accessors returned nothing, the extracted code was
`""`, and the valid-code list never contains `""`: **every** order was marked
invalid, including valid codes such as `YORB`. The script now navigates by local
name:

```groovy
xml.Body.notifications.Notification.sObject.giic_OrderTypeCode__c.text()
```

### Why the fixtures needed no change

`valid_order_yorb.assert.json` expected `OrderCodeValid = true`, which is what the
flow is *supposed* to do, so the harness was red until the iFlow was corrected and
turned green on its own. That is the property worth keeping: the fixtures describe
the intended behaviour, not the observed one, so they cannot silently ratify a bug.

It also matters that the previous pipeline never executed the Groovy at all — the
generated tests simulated the rule in Python — so neither defect could surface.
`tests/test_groovy_harness.py::test_scan_all_on_the_real_artifact_passes` now guards
both fixes and will fail if either returns.

