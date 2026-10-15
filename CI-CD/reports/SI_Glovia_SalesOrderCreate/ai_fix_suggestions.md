# Suggested fixes — SI_Glovia_SalesOrderCreate

Generated from the pytest failure log of commit `7fc841394459`.

> Written by a model from the iFlow XML, its scripts and the failure log. It is a hypothesis to speed up the investigation, not a verdict: check it against the artifact before changing anything.

---

### 1. Analysis of the Pytest Failure

The test `test_tc_glovia_003_escalation_default_route` failed with:
```text
> assert fault_string is not None and "RoutingConditionNotMet" in fault_string.text
E assert (None is not None)
```

#### Cause of the Failure:
1. **XML Namespace Mismatch in SOAP Fault Parsing:**
   In the mock callback inside the test, the returned payload is:
   ```xml
   <soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
     <soapenv:Body>
       <soapenv:Fault>
         <faultcode>soapenv:Server</faultcode>
         <faultstring>Escalation End: RoutingConditionNotMet</faultstring>
       </soapenv:Fault>
     </soapenv:Body>
   </soapenv:Envelope>
   ```
   Under the SOAP 1.1 specification, sub-elements of `<Fault>` (`<faultcode>`, `<faultstring>`) reside in the **unqualified (empty) namespace** by default unless explicitly given the `soapenv:` prefix.
   The test executed:
   ```python
   fault_string = root.find(".//{http://schemas.xmlsoap.org/soap/envelope/}faultstring")
   ```
   Because `<faultstring>` had no namespace, `root.find()` returned `None`.

---

### 2. Issues & Necessary Fixes in SAP Integration Suite (IFlow XML)

The test failed because of a disconnect between how the IFlow handles the unrouted escalation scenario versus how the sender expects the error/escalation to be returned.

#### A. Incompatible Message Exchange Pattern (MEP)
In `MessageFlow_114` (Sender SOAP Adapter) and `Collaboration_1`:
```xml
<ifl:property>
    <key>messageExchangePattern</key>
    <value>OneWay</value>
</ifl:property>
...
<ifl:property>
    <key>returnExceptionToSender</key>
    <value>false</value>
</ifl:property>
```
* **Issue:** The SOAP sender adapter is set to `OneWay` (Asynchronous / Fire-and-Forget) and `returnExceptionToSender = false`. A `OneWay` SOAP channel will return an HTTP `202 Accepted` (or HTTP 200 with empty body) to the sender upon message ingestion. It cannot return synchronous HTTP `500` SOAP Faults back to the caller.
* **Fix in IFlow XML:**
  Change the adapter configuration to `RequestReply` and allow returning exceptions to the sender:
  ```xml
  <ifl:property>
      <key>messageExchangePattern</key>
      <value>RequestReply</value>
  </ifl:property>
  <ifl:property>
      <key>returnExceptionToSender</key>
      <value>true</value>
  </ifl:property>
  ```

#### B. Escalation End Event vs. Error End Event
In `Process_1`:
```xml
<bpmn2:endEvent id="EndEvent_11" name="Escalation End">
    <bpmn2:escalationEventDefinition>
        <bpmn2:extensionElements>
            <ifl:property>
                <key>ErrorCode</key>
                <value>RoutingConditionNotMet</value>
            </ifl:property>
            <ifl:property>
                <key>activityType</key>
                <value>EscalationEndEvent</value>
            </ifl:property>
        </bpmn2:extensionElements>
    </bpmn2:escalationEventDefinition>
</bpmn2:endEvent>
```
* **Issue:** In SAP Cloud Integration, an `EscalationEndEvent` marks the message processing status as `ESCALATED` in the Message Processing Log (MPL), but it **does not throw a runtime exception** and does not generate an HTTP 500 SOAP Fault to the sender.
* **Fix in IFlow XML:**
  If the requirement is to fail the integration and return an HTTP 500 SOAP Fault with error code `RoutingConditionNotMet`:
  Replace `EndEvent_11` with an **Error End Event**:
  ```xml
  <bpmn2:endEvent id="EndEvent_11" name="Error End">
      <bpmn2:incoming>SequenceFlow_110</bpmn2:incoming>
      <bpmn2:errorEventDefinition id="ErrorEventDefinition_11">
          <bpmn2:extensionElements>
              <ifl:property>
                  <key>activityType</key>
                  <value>EndErrorEvent</value>
              </ifl:property>
              <ifl:property>
                  <key>cmdVariantUri</key>
                  <value>ctype::FlowstepVariant/cname::ErrorEndEvent</value>
              </ifl:property>
          </bpmn2:extensionElements>
      </bpmn2:errorEventDefinition>
  </bpmn2:endEvent>
  ```

#### C. Custom SOAP Fault Payload Generation (Content Modifier)
If the sender specifically expects an explicit SOAP 1.1 fault body containing the string `Escalation End: RoutingConditionNotMet`, add a **Content Modifier** prior to the error/escalation end event:
* **Body Type:** `Expression` / `Constant`
* **Body:**
  ```xml
  <soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
    <soapenv:Body>
      <soapenv:Fault>
        <faultcode>soapenv:Server</faultcode>
        <faultstring>Escalation End: RoutingConditionNotMet</faultstring>
      </soapenv:Fault>
    </soapenv:Body>
  </soapenv:Envelope>
  ```
* **Header:** Set `Content-Type = text/xml; charset=utf-8` and `HTTP.ResponseCode = 500`.

---

### 3. Corresponding Fix in the Test Script

If updating the test generator or test script directly, ensure namespace consistency when asserting SOAP Fault sub-elements:

```python
# Option 1: Search without namespace qualification (standard SOAP 1.1 faultstring)
fault_string = root.find(".//faultstring")
assert fault_string is not None and "RoutingConditionNotMet" in fault_string.text

# Option 2: Prefix faultstring in the mocked body if the qualified form was expected
body = (
    '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
    "<soapenv:Body><soapenv:Fault><soapenv:faultcode>soapenv:Server</soapenv:faultcode>"
    "<soapenv:faultstring>Escalation End: RoutingConditionNotMet</soapenv:faultstring>"
    "</soapenv:Fault></soapenv:Body></soapenv:Envelope>"
)
```
