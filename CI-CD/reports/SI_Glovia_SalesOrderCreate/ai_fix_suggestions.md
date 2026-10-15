# Suggested fixes — SI_Glovia_SalesOrderCreate

Generated for commit `2d613c16ac42` from the failure log.

> Written by a model from the iFlow XML and its scripts. It is a hypothesis to speed up the investigation, not a verdict: check it against the artifact before changing anything.

---

### Failure and Test Analysis

Based on `pytest_logs.txt` and `groovy_logs.txt`, all executed test cases passed:
* **Pytest Suite:** 5 passed, 0 failed.
* **Groovy Harness:** 4 passed, 0 failed.

There are no runtime execution or syntax failures in the Groovy scripts or the active execution paths simulated by pytest. However, the quality/coverage gate failed due to **unreachable components** within the iFlow definition.

#### Layer Identification
* **Defect Layer:** **iFlow XML (BPMN Model)**
* **Root Cause:** `CallActivity_77` (executing `GLOVIA_Conditioncheck_Salesorder_Create.groovy`) and `CallActivity_86` (Content Modifier) are unattached floating shapes without incoming or outgoing BPMN `<bpmn2:sequenceFlow>` connections. As a result, routing decisions downstream at `ExclusiveGateway_80` that evaluate conditions like `${header.OrderCodeValid} = 'true'` depend on headers that are never dynamically evaluated at runtime by the flow, causing the flow to either fail or rely on external injection.

---

## Fix Risks

1. **Unattached Process Activities (CallActivity_86 and CallActivity_77)**
   * **Fix in iFlow:** Connect `CallActivity_86` and `CallActivity_77` into the sequence flow in `Process_1`. Wire the outgoing sequence flow from `CallActivity_4` (Set Message Headers) to `CallActivity_86`, then add a sequence flow from `CallActivity_86` to `CallActivity_77`, and connect the outgoing sequence flow of `CallActivity_77` to `ParallelGateway_74` (or directly before `ExclusiveGateway_80`). This ensures `OrganizationId` is extracted and `GLOVIA_Conditioncheck_Salesorder_Create.groovy` executes to populate `${header.OrderCodeValid}` before gateway routing.

2. **Input Payload Memory Bloat via Expression Property**
   * **Fix / Mitigation:** In `CallActivity_4` (or wherever `InputPayload` is captured), storing `${in.body}` as a property duplicates the message in memory. 
     * *Mitigation in iFlow:* In `MessageFlow_114`, verify and enforce `<key>maximumBodySize</key><value>40</value>`.
     * *Optimization in iFlow:* If the payload is only required for error logging in `SubProcess_91`, avoid retaining large string properties in memory for the entire happy path. Instead, let `SubProcess_91` access the payload directly or configure payload persistence only when entering exception flows, or lower `maximumBodySize` on `MessageFlow_114` to a threshold safe for the tenant heap limit (e.g., 10–20 MB).

3. **Escalation End vs One-Way Message Exchange Pattern**
   * **Fix / Mitigation:** In `MessageFlow_114`, `messageExchangePattern` is set to `OneWay`, while `returnExceptionToSender` is set to `false` in `Collaboration_1`. When `EndEvent_11` (`EscalationEndEvent`) triggers with `ErrorCode: RoutingConditionNotMet`, the sender receives an HTTP 202 Accepted, masking the routing failure.
     * *Mitigation in iFlow:* Ensure `Interface_Generic_Headers.groovy` sets MPL custom header properties (`Country`, `Region`, `BusinessObject`, etc.) before reaching `EndEvent_11` so operations can query escalation events in the Message Processing Log.
     * *Alternative fix:* If the sender requires synchronous error acknowledgment, change `messageExchangePattern` to `RequestReply` on `MessageFlow_114` and set `returnExceptionToSender` to `true` in `Collaboration_1`.

4. **Groovy 4 XmlSlurper Compatibility in Script Assets**
   * **Status:** **Already covered by the current design.**
   * *Evidence:* `GLOVIA_Conditioncheck_Salesorder_Create.groovy` already contains the explicit import `import groovy.xml.XmlSlurper` and navigates XML elements using local element names (`xml.Body.notifications.Notification.sObject.giic_OrderTypeCode__c.text()`) rather than namespace prefixes. Both Groovy harness test cases (`valid_order_yorb` and `invalid_order_code`) compiled and passed successfully.

---

## Fix Coverage

### Uncovered Components (0 % Coverage)
* **`Floating_Activities_CallActivity_77_86`** (0 %)
* **`CallActivity_77 (Groovy Script 2)`** (0 %)
* **`CallActivity_86 (Content Modifier)`** (0 %)

### Concrete Actions to Achieve 100 % Coverage

1. **iFlow XML Sequence Flow Wiring:**
   * In `Process_1`, wire sequence flows to include both elements:
     * Redirect the outgoing sequence flow of `CallActivity_4` to target `CallActivity_86`.
     * Create a sequence flow from `CallActivity_86` to `CallActivity_77`.
     * Create a sequence flow from `CallActivity_77` to `ParallelGateway_74`.
   * *Alternative (if obsolete):* If `CallActivity_86` and `CallActivity_77` are deprecated remnants that have been superseded by other logic, remove `<bpmn2:callActivity id="CallActivity_77">`, `<bpmn2:callActivity id="CallActivity_86">`, and their corresponding `<bpmndi:BPMNShape>` entries completely from the XML artifact.

2. **Pytest Test Additions:**
   * Once wired into the flow, add test iterations to `test_flow.py`:
     * **Valid Order Codes:** Add parametrized tests sending SOAP payloads containing `YORB`, `YICB`, `POR`, `MNZ`, and `PLM` in `giic_OrderTypeCode__c`, verifying that `CallActivity_77` sets `OrderCodeValid = true` and routes to `CallActivity_102` / `Participant_2`.
     * **Invalid Order Codes:** Add a test sending an unlisted code (e.g., `INVALID_CODE`), verifying that `CallActivity_77` sets `OrderCodeValid = false` and routes to `EndEvent_11` (`EscalationEndEvent`).
     * **OrganizationId Property Assertion:** Add an assertion verifying that `CallActivity_86` successfully populates the property/header `OrganizationId` from the inbound payload.
