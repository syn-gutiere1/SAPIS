# Suggested fixes — SI_Glovia_SalesOrderCreate

Generated for commit `a4ec270da3da` from the failure log.

> Written by a model from the iFlow XML and its scripts. It is a hypothesis to speed up the investigation, not a verdict: check it against the artifact before changing anything.

---

### Failure Analysis and Layer Identification

* **Layer Analysis**:
  * **Pytest Suite Layer**: **PASSED** (5 passed, 0 failed). The simulated routing, downstream calls, escalation routing, and error subprocess logic passed in simulation.
  * **Groovy Harness Layer**: **PASSED** (4 passed, 0 failed). All individual scripts (`GLOVIA_Conditioncheck_Salesorder_Create.groovy`, `Interface_Generic_Headers.groovy`, and `Log_Payload.gsh`) compiled successfully under Groovy 4 and passed all unit test fixtures.
  * **Test Harness / Test Data Layer**: No failures detected.
  * **iFlow BPMN Layer / CI Quality Gate**: **FAILED**. The CI pipeline failed due to a branch/component coverage threshold gate (< 100% target coverage). Specifically, `CallActivity_77` and `CallActivity_86` are defined within `<bpmn2:process id="Process_1">` in the iFlow XML but have **no incoming or outgoing `<bpmn2:sequenceFlow>`**. Because they are floating/disconnected elements, the runtime engine never executes them, making 100% test coverage impossible.

---

### Recommended Fixes in the iFlow XML

In `Process_1` of the iFlow XML:
1. **Connect the Orphaned Activities**:
   * Determine where the order type validation and organization ID extraction belong. Based on the flow architecture, they should execute after the initial message header extraction (`CallActivity_4`) and before the conditional routing (`ExclusiveGateway_80`).
   * Add incoming and outgoing sequence flows in BPMN:
     * Retarget the outgoing sequence flow from `CallActivity_4` to point to `CallActivity_86`.
     * Add a sequence flow connecting `CallActivity_86` to `CallActivity_77`.
     * Connect `CallActivity_77` to `ParallelGateway_74` or `ExclusiveGateway_80`.
2. **Alternative (if obsolete)**:
   * If `CallActivity_77` and `CallActivity_86` represent legacy logic superseded by router externalized parameters, delete both `<bpmn2:callActivity id="CallActivity_77">` and `<bpmn2:callActivity id="CallActivity_86">` (along with their corresponding `<bpmndi:BPMNShape>` visual entries) from the iFlow XML.

---

## Fix Risks

1. **Unattached Process Activities (CallActivity_77 and CallActivity_86)**
   * **Fix in iFlow**: In `Process_1`, wire `CallActivity_86` (Content Modifier) and `CallActivity_77` (Groovy script `GLOVIA_Conditioncheck_Salesorder_Create.groovy`) into the main sequence flow. Specifically, re-route the sequence flow coming out of `CallActivity_4` to enter `CallActivity_86`, connect `CallActivity_86` to `CallActivity_77`, and route `CallActivity_77` into `ExclusiveGateway_80`. Ensure `ExclusiveGateway_80` evaluates the resulting header `${header.OrderCodeValid} = 'true'`.

2. **Message Body Size & Memory Consumption**
   * **Status**: **Already covered by current design.**
   * **Details**: The sender SOAP adapter `MessageFlow_114` explicitly restricts inbound message payload through `<ifl:property><key>maximumBodySize</key><value>40</value></ifl:property>` (capped at 40 MB) and attachment size to 100 MB. In CallActivity_4, storing `${in.body}` into property `InputPayload` operates within this configured 40 MB ceiling. No iFlow edit is required.

3. **Escalation End Event Handling**
   * **Fix / Mitigation**: `EndEvent_11` uses an `EscalationEndEvent` with `ErrorCode` = `RoutingConditionNotMet`. Because the inbound sender adapter is one-way SOAP over HTTP (`MessageFlow_114`: `<key>messageExchangePattern</key><value>OneWay</value>`), CPI terminates the exchange with an escalation status. To prevent sender retries from Glovia when condition routing fails, either:
     * In the iFlow: Replace `EndEvent_11` with a Message End Event preceded by a Content Modifier that logs/acknowledges the dropped order if non-retryable discards are required by the business contract.
     * At the architecture/system level: Mitigate by configuring Glovia sender retry policies to classify HTTP 500 / Escalation faults matching `RoutingConditionNotMet` as non-transient, business-rejected messages rather than retryable network failures.

4. **Externalized Configuration Parameters Missing**
   * **Mitigation**: Router gateway `ExclusiveGateway_80` evaluates externalized properties `{{Glovia_Route_SalesOrder}}` and `{{Glovia_Route2_ReturnSalesOrder}}`. This cannot be resolved statically inside the iFlow XML since externalized parameters are injected per tenant environment. Mitigate this in the deployment pipeline by introducing a pre-deployment configuration check that validates that `parameters.prop` contains non-empty boolean expressions (e.g., `//giic_OrderTypeCode__c != ''`) for both routing tokens before deployment.

---

## Fix Coverage

* **CallActivity_77_Floating_Groovy / CallActivity_77 (Groovy Script 2) (0%)**:
  * **Fix in iFlow**: Wire `CallActivity_77` into the sequence flow between `CallActivity_86` and `ExclusiveGateway_80` (or remove `CallActivity_77` from `Process_1` if the validation is no longer part of the integration design).
  * **Test Coverage**: Once wired, add two test cases in `test_flow.py`:
    1. A payload with `<giic_OrderTypeCode__c>` set to a valid code (`YORB`, `YICB`, `POR`, `MNZ`, or `PLM`), asserting that header `OrderCodeValid` is set to `'true'` and proceeds down `SequenceFlow_82`.
    2. A payload with an invalid order type code (e.g., `INVALID`), asserting that `OrderCodeValid` is set to `'false'` and routes to `EndEvent_11`.

* **CallActivity_86_Floating_ContentModifier / CallActivity_86 (Content Modifier) (0%)**:
  * **Fix in iFlow**: Wire `CallActivity_86` directly after `CallActivity_4` with an incoming `<bpmn2:sequenceFlow>` from `CallActivity_4` and an outgoing `<bpmn2:sequenceFlow>` to `CallActivity_77` (or remove `CallActivity_86` from `Process_1` if redundant).
  * **Test Coverage**: Once wired, add an assertion in `test_flow.py` verifying that header `OrganizationId` is successfully extracted from XPath `/ns1:notifications/ns1:Notification/ns1:sObject/ns2:giic_OrderTypeCode__c` during message processing.

* **Uncovered Paths (Orphaned / Unreachable elements CallActivity_77 and CallActivity_86)**:
  * Since neither element has inbound or outbound sequence flows, the BPMN engine cannot traverse them. Wire both elements sequentially into `Process_1`:
    `CallActivity_4` $\rightarrow$ `CallActivity_86` $\rightarrow$ `CallActivity_77` $\rightarrow$ `ParallelGateway_74` / `ExclusiveGateway_80`.
  * If these steps were abandoned during a refactoring to tenant externalized parameters, completely remove `<bpmn2:callActivity id="CallActivity_77">` and `<bpmn2:callActivity id="CallActivity_86">` from the XML artifact to achieve 100% reachable path coverage.
