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

## The defect these fixtures expose

`GLOVIA_Conditioncheck_Salesorder_Create.groovy` marks **every** order as invalid,
including valid codes such as `YORB`.

The script navigates the XML with namespace-prefixed accessors:

```groovy
xml.'ns1:notifications'.'ns1:Notification'.'ns1:sObject'.'ns2:giic_OrderTypeCode__c'.text()
```

`XmlSlurper` **discards namespace prefixes** when exposing element names —
`xml.name()` returns `Envelope`, not `SOAP:Envelope` — and that holds even with
`namespaceAware = true`. Those accessors therefore return nothing, the extracted
code is `""`, and the valid-code list never contains `""`.

The correct navigation uses the **local names**:

```groovy
xml.Body.notifications.Notification.sObject.giic_OrderTypeCode__c.text()
```

### Why the fixtures describe the correct behaviour

`valid_order_yorb.assert.json` expects `OrderCodeValid = true`, which is what the
flow is *supposed* to do. The harness runs the real script, so the Groovy job fails
until the iFlow is fixed.

This is deliberate. The previous pipeline never executed the Groovy: the generated
tests simulated the rule in Python, so this defect never surfaced. A red check here
is the evidence.

When the script is corrected in the iFlow, these fixtures need no change — the job
turns green on its own.
