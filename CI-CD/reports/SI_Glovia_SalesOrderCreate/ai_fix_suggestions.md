# Suggested fixes — SI_Glovia_SalesOrderCreate

Generated for commit `1a58430f2313` from the failure log.

> Written by a model from the iFlow XML and its scripts. It is a hypothesis to speed up the investigation, not a verdict: check it against the artifact before changing anything.

---

### Analysis of Test Failures

#### 1. Test Harness and Groovy Execution Layer
* **Status**: **PASSED** (4 passed, 0 failed).
* **Details**: All Groovy unit tests (`GLOVIA_Conditioncheck_Salesorder_Create`, `Interface_Generic_Headers`, and `Log_Payload`) compiled and passed successfully under the test harness. No compilation errors, missing imports, or missing method exceptions occurred.

#### 2. Generated Test Layer (`test_flow.py`)
* **Status**: **FAILED** (2 tests failed: `test_adapter_inbound_simulation` and `test_adapter_timeout_simulation`).
* **Root Cause Identification**:
  * Both test failures produced `requests.exceptions.ConnectionError: Connection refused by Responses - the call doesn't match any registered mock.`
  * The log shows:
    ```text
    Request: 
    - POST http://localhost/cxf/Glovia_SalesOrder

    Available matches:
    - POST http://localhost:0/cxf/Glovia_SalesOrder URL does not match
    ```
  * In the generated test file `CI-CD/tests/SI_Glovia_SalesOrderCreate/test_flow.py`, `TEST_ENDPOINT` was defined using port `0` (e.g., `http://localhost:0/cxf/...`), which `responses` registers verbatim. However, Python's `requests` / `urllib3` library normalizes the URL when dispatching the request and strips port `:0`, sending `http://localhost/cxf/...`. Because `responses` performs strict URL matching by default, it rejects the request as unmatched.
* **Layer Attribution**: **Generated Test Defect**.
* **Remediation**: This is entirely an issue in the AI-generated test file, not the iFlow XML or Groovy scripts. No changes should be made to the iFlow or Groovy scripts for these failure logs. In `test_flow.py`, `TEST_ENDPOINT` should be initialized with a standard mock port (such as `http://localhost:8080/cxf/...`) or without an explicit `:0` port definition.

---

## Fix Risks

### 1. Orphaned Content Modifier (CallActivity_86) and Groovy Script (CallActivity_77)
* **How to fix in iFlow**:
  * In the iFlow BPMN XML (`Process_1`), reconnect the sequence flows to place `CallActivity_77` (referencing `GLOVIA_Conditioncheck_Salesorder_Create.groovy`) and `CallActivity_86` into the main execution path.
  * Specifically, update the outgoing flow from `CallActivity_71` (`Call Headers`) to target `CallActivity_77`, route `CallActivity_77`'s outgoing flow into `CallActivity_86`, and then connect `CallActivity_86`'s outgoing sequence flow directly to `ExclusiveGateway_80` (the routing gateway).
  * Update the gateway routing conditions on the branches departing `ExclusiveGateway_80` to evaluate the header `${header.OrderCodeValid} = 'true'` set by `GLOVIA_Conditioncheck_Salesorder_Create.groovy`, ensuring invalid order types are cleanly routed to `EndEvent_11` (`Escalation End`).

### 2. Sensitive Data Leakage via Error Subprocess Logging (Log_Payload.gsh)
* **How to fix in Groovy script**:
  * Update `script/Log_Payload.gsh` in `CallActivity_100` (`Log Payload`) to mask sensitive fields before writing to the MPL attachment.
  * Instead of storing raw XML directly (`messageLog.addAttachmentAsString("Payload", body, "text/plain")`), apply regex replacement or an XML transform on `body` to scrub sensitive attributes (such as pricing, credit, or customer PII):
    ```groovy
    def sanitizedBody = body.replaceAll(/(?i)<(ns2:Amount|UnitPrice|TotalPrice)>.*?<\/\1>/, '<$1>***MASKED***</$1>')
    messageLog.addAttachmentAsString("Payload", sanitizedBody, "text/plain")
    ```
  * Alternatively, in `CallActivity_97` (`Read the payload from property`), avoid passing the entire unmasked raw payload if storing it in MPL attachments is not required in production.

### 3. SOAP Receiver Endpoint Communication Timeout
* **How to fix in iFlow**:
  * The outbound communication uses HTTP/REST receivers (`Participant_2` and `Participant_112`).
  * In the corresponding HTTP/REST outbound channels (`MessageFlow` definitions for `Participant_2` and `Participant_112`), configure explicit timeout thresholds: set `Timeout (in ms)` (property `httpAddressTimeout` or `requestTimeout`) to a reasonable upper limit (e.g., `30000` ms) to prevent hung worker threads.
  * Ensure the receiver adapter's error handling does not swallow HTTP 504 / socket timeout exceptions so that they bubble up and trigger the error handling path (or `SubProcess_91`).

### 4. Externalized Parameter Resolution Failure
* **Coverage / How to mitigate in iFlow**:
  * **Current Design Assessment**: The flow design relies on externalized parameters `{{Glovia_Route_SalesOrder}}` and `{{Glovia_Route2_ReturnSalesOrder}}` within the router configuration branches of `ExclusiveGateway_80`. 
  * **How to fix / mitigate**:
    * Provide deterministic default routing: ensure `SequenceFlow_110` leading to `EndEvent_11` (`Escalation End` with error code `RoutingConditionNotMet`) is explicitly flagged as the `default` sequence flow on `ExclusiveGateway_80` (`<bpmn2:exclusiveGateway default="SequenceFlow_110" ...>`).
    * In the project configuration file `parameters.propdef`, ensure `{{Glovia_Route_SalesOrder}}` and `{{Glovia_Route2_ReturnSalesOrder}}` have non-empty default fallback values defined so deployment validation fails fast if tenant parameters are missing.
