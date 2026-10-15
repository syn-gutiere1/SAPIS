# Suggested fixes — SI_Glovia_SalesOrderCreate

Generated from the failure log of commit `02d049eda118`.

> Written by a model from the iFlow XML, its scripts and the failure log (pytest, the Groovy harness, or both). It is a hypothesis to speed up the investigation, not a verdict: check it against the artifact before changing anything.

---

### Root Cause Analysis

The test execution failure is caused by a Groovy compilation error (`MultipleCompilationErrorsException`) in script `script/GLOVIA_Conditioncheck_Salesorder_Create.groovy`:

```text
unable to resolve class XmlSlurper
 @ line 5, column 15.
       def xml = new XmlSlurper().parseText(body)
```

The script instantiates `new XmlSlurper()` without explicitly importing the class. While some Groovy runtime environments provide default star-imports for `groovy.util.*`, standard CI/CD test runners and Groovy 3+ runtimes require an explicit import (`groovy.xml.XmlSlurper` or `groovy.util.XmlSlurper`).

---

### Solution

#### Update `script/GLOVIA_Conditioncheck_Salesorder_Create.groovy`

Add the import `import groovy.xml.XmlSlurper` (or `import groovy.util.XmlSlurper`) at the top of the script.

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

*(Optional Note: If the XML payload contains namespaces and prefix resolution fails during parsing, consider disabling namespace awareness via `new XmlSlurper(false, false).parseText(body)` or declaring `declareNamespace` on the slurper).*
