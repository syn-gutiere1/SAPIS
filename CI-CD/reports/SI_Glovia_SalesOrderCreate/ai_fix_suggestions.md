# Suggested fixes — SI_Glovia_SalesOrderCreate

Generated for commit `fa5528c9d916` from the failure log.

> Written by a model from the iFlow XML and its scripts. It is a hypothesis to speed up the investigation, not a verdict: check it against the artifact before changing anything.

---

### Analysis of Test Failures and Component Layers

1. **Pytest Suite (`pytest_logs.txt`)**:
   - **Result**: 5 passed, 0 failed.
   - **Layer**: Generated Python Simulation Tests.
   - **Status**: Clean.

2. **Groovy Test Suite (`groovy_logs.txt`)**:
   - **Failure**: `[FAIL] Log_Payload :: payload (31 ms) -> attachment 'Payload' does not contain 'NAO-EXISTE'`.
   - **Layer**: Test Fixture (`CI-CD/groovy-fixtures/Log_Payload/payload.assert.json` or equivalent test assertion).
   - **Analysis**:
     Examining `script/Log_Payload.gsh`:
     ```groovy
     def Message processData(Message message) {
         def body = message.getBody(java.lang.String) as String;
         def messageLog = messageLogFactory.getMessageLog(message);
         messageLog.addAttachmentAsString("Payload", body, "text/plain");
         return message;
     }
     ```
     The script simply extracts the incoming body as a string and attaches it to the MPL under the name `"Payload"`.
     The assertion fails because the test fixture asserts that the attachment contains the dummy string `'NAO-EXISTE'` (Portuguese for "DOES-NOT-EXIST"). This is an erroneous test assertion/dummy fixture artifact within the Groovy test suite fixture, not a defect in `Log_Payload.gsh` or the iFlow artifact.
   - **Proposed Action**: No code changes to `Log_Payload.gsh` or the BPMN XML for this failure. Update or correct the fixture assertion for `Log_Payload :: payload` so that it expects real payload content matching the injected test case rather than `'NAO-EXISTE'`.

---

## Fix Risks

### 1. Orphaned Groovy Script and Content Modifier steps (`CallActivity_77` and `CallActivity_86`) not connected to BPMN sequence flow
* **Risk & Consequence**: Business logic for order code validation (`GLOVIA_Conditioncheck_Salesorder_Create.groovy`) and `OrganizationId` header extraction is completely disconnected from the sequence flow, allowing unvalidated order codes to proceed.
* **Fix in iFlow XML**: 
  - Wire `CallActivity_86` and `CallActivity_77` sequentially into the main process sequence flow between the initial Content Modifier (`CallActivity_4`) and the Parallel Multicast Gateway (`ParallelGateway_74`).
  - Delete direct incoming flow to `ParallelGateway_74` originating from `CallActivity_4`.
  - Connect `CallActivity_4` $\rightarrow$ `CallActivity_86` via sequence flow.
  - Connect `CallActivity_86` $\rightarrow$ `CallActivity_77` via sequence flow.
  - Connect `CallActivity_77` $\rightarrow$ `ParallelGateway_74` via sequence flow.
  - In `ExclusiveGateway_80`, update routing conditions to evaluate `${header.OrderCodeValid} = 'true'` alongside the route conditions.

### 2. Message loss due to One-Way SOAP Message Exchange Pattern (MEP) with `returnExceptionToSender=false`
* **Risk & Consequence**: The sender receives an immediate HTTP 202 Ack upon message acceptance. Any downstream routing error (e.g., `EscalationEndEvent` `EndEvent_11`) or unhandled processing exception produces silent failures with no sender notification.
* **Fix / Mitigation**: 
  - In Collaboration properties, change `returnExceptionToSender` from `false` to `true` if the sender system supports fault contracts.
  - If the sender interface requires an asynchronous one-way contract (`messageExchangePattern = OneWay`), mitigate this at the iFlow design level by:
    1. Retaining `SubProcess_91` error handling and logging via `Log_Payload.gsh` with custom MPL status and alerts.
    2. Modifying `EndEvent_11` (`EscalationEndEvent`) to route unroutable messages to an error alert queue/dead-letter topic or notifying operations via an alert channel, rather than silently completing.

### 3. Uninitialized / Unbound externalized parameters in gateway condition expressions (`{{Glovia_Route_SalesOrder}}` and `{{Glovia_Route2_ReturnSalesOrder}}`)
* **Risk & Consequence**: If tenant configurations lack parameter definitions, condition expressions evaluate to false or raise syntax parsing errors, routing all traffic to `EndEvent_11`.
* **Fix in iFlow**:
  - In `src/main/resources/parameters.propdef` and the flow configuration (`configurations.prop`), define explicit default boolean or regex fallback values (e.g., `Glovia_Route_SalesOrder = true`, `Glovia_Route2_ReturnSalesOrder = true`).
  - In `ExclusiveGateway_80`, ensure standard Apache Camel simple expression guards are used rather than raw unvalidated placeholders, or configure explicit default branch routing to handle unmatched scenarios gracefully.

### 4. Payload tampering or loss during exception handling in `SubProcess_91`
* **Risk & Consequence**: If an unhandled exception occurs prior to `CallActivity_4` storing `${in.body}` into `property.InputPayload`, `CallActivity_97` (`Read the payload from property`) replaces the body with `${property.InputPayload}` (which evaluates to null/empty), wiping out error context before `Log_Payload.gsh` runs.
* **Fix in iFlow**:
  - In `CallActivity_4`, ensure initial property setting runs as step 1 immediately after `StartEvent_2`.
  - In `CallActivity_97`, mitigate body loss by using a fallback expression in `wrapContent`: `${property.InputPayload != null ? property.InputPayload : in.body}` or configure an initial Groovy script/enricher that checks whether `property.InputPayload` is set before replacing `${in.body}`.

---

## Fix Coverage

### Uncovered Components (Below 100%)
* **`CallActivity_77 (Groovy Script 2)` - Coverage: 0%**
* **`CallActivity_86 (Content Modifier)` - Coverage: 0%**

### Uncovered Paths & Remediation

1. **Path: `CallActivity_77` (GLOVIA_Conditioncheck_Salesorder_Create.groovy) and `CallActivity_86` (Content Modifier extracting OrganizationId)**
   - **Root Cause**: Both elements are orphaned in the BPMN model with zero incoming or outgoing sequence flows (`<bpmn2:incoming>` and `<bpmn2:outgoing>` missing).
   - **Fix**:
     - Do not delete these elements as they contain critical validation logic (`GLOVIA_Conditioncheck_Salesorder_Create.groovy` and header extraction for `OrganizationId`).
     - Wire them into the active execution path of `Process_1`:
       1. Add `bpmn2:sequenceFlow` from `CallActivity_4` to `CallActivity_86`.
       2. Add `bpmn2:sequenceFlow` from `CallActivity_86` to `CallActivity_77`.
       3. Add `bpmn2:sequenceFlow` from `CallActivity_77` to `ParallelGateway_74`.
     - Once connected, add unit tests in `CI-CD/tests/SI_Glovia_SalesOrderCreate/test_flow.py` covering:
       - Payload with valid order codes (`YORB`, `YICB`, `POR`, `MNZ`, `PLM`) exercising `CallActivity_77` setting `OrderCodeValid = true`.
       - Payload with invalid order codes (e.g., `INVALID`) asserting that `OrderCodeValid = false` routes to the default/escalation branch.
       - Message payload containing `OrganizationId` ensuring `CallActivity_86` sets the target header.
