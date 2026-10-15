# Suggested fixes — SI_Glovia_SalesOrderCreate

Generated from the failure log of commit `d7584e195851`.

> Written by a model from the iFlow XML, its scripts and the failure log (pytest, the Groovy harness, or both). It is a hypothesis to speed up the investigation, not a verdict: check it against the artifact before changing anything.

---

Based on the provided failure logs and source code, there are two distinct issues causing the failures: a compilation error in the Groovy script and a signature mismatch in the test framework's mock dispatcher.

---

### 1. Groovy Script Failure: Missing Import for `XmlSlurper`

#### **Root Cause**
In `script/GLOVIA_Conditioncheck_Salesorder_Create.groovy`, the script references `XmlSlurper` directly without importing it:
```
MultipleCompilationErrorsException: startup failed:
unable to resolve class XmlSlurper
```
In standard Groovy / SAP Cloud Integration runtime environments, `XmlSlurper` is not automatically imported in all runner contexts and must be imported explicitly.

#### **Fix**
Add `import groovy.xml.XmlSlurper` at the top of `script/GLOVIA_Conditioncheck_Salesorder_Create.groovy`.

**Updated Script (`GLOVIA_Conditioncheck_Salesorder_Create.groovy`):**
```groovy
import com.sap.gateway.ip.core.customdev.util.Message
import groovy.xml.XmlSlurper

def Message processData(Message message) {
    def body = message.getBody(String)
    def xml = new XmlSlurper().parseText(body)

    def orderCode = xml.'ns1:notifications'.'ns1:Notification'.'ns1:sObject'.'ns2:giic_OrderTypeCode__c'.text()
    def validCodes = ['YORB', 'YICB', 'POR', 'MNZ', 'PLM']

    if (validCodes.contains(orderCode)) {
        message.setHeader("OrderCodeValid", "true")
    } else {
        message.setHeader("OrderCodeValid", "false")
    }

    return message
}
```

*(Note: If namespace awareness causes lookup issues with prefixed tags like `'ns1:notifications'` after parsing, you can instantiate the parser as `new XmlSlurper(false, false).parseText(body)` to ignore namespaces and query tags directly).*

---

### 2. Pytest Failure: Mock Signature Mismatch (`mock_cpi_pipeline_dispatch`)

#### **Root Cause**
All 4 failing Python tests fail with:
```
TypeError: mock_cpi_pipeline_dispatch() got an unexpected keyword argument 'timeout'
```
When `requests.Session.send(prep, **send_kwargs)` is invoked by `flow_client.execute(...)`, `requests` forwards internal keyword arguments (such as `timeout`, `proxies`, `stream`, `verify`, `cert`). The mock function `mock_cpi_pipeline_dispatch` defined in `CI-CD/tests/SI_Glovia_SalesOrderCreate/test_flow.py` only accepts specific parameters (e.g. `(request)`), causing Python to throw a `TypeError`.

#### **Fix**
Update the function definition of `mock_cpi_pipeline_dispatch` in `CI-CD/tests/SI_Glovia_SalesOrderCreate/test_flow.py` to accept variable keyword arguments (`**kwargs`):

**Before:**
```python
def mock_cpi_pipeline_dispatch(request):
    ...
```

**After:**
```python
def mock_cpi_pipeline_dispatch(request, *args, **kwargs):
    ...
```
