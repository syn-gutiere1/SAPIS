# Suggested fixes — SI_Glovia_SalesOrderCreate

Generated from the failure log of commit `21f71b1a6737`.

> Written by a model from the iFlow XML, its scripts and the failure log (pytest, the Groovy harness, or both). It is a hypothesis to speed up the investigation, not a verdict: check it against the artifact before changing anything.

---

### Root Cause Analysis

The test execution failure is caused by a Groovy compilation error (`MultipleCompilationErrorsException`) in **`script/GLOVIA_Conditioncheck_Salesorder_Create.groovy`**:

```text
GLOVIA_Conditioncheck_Salesorder_Create.groovy: 5: unable to resolve class XmlSlurper
 @ line 5, column 15.
       def xml = new XmlSlurper().parseText(body)
```

The class `XmlSlurper` is not implicitly imported in modern Groovy runtimes (Groovy 3.x+ / JDK 17 environments used in the test runner) or when modular Groovy packages are evaluated. As a result, the script fails during compilation because `XmlSlurper` cannot be resolved without an explicit `import` statement.

---

### Solution

Add the import statement for `XmlSlurper` at the top of `script/GLOVIA_Conditioncheck_Salesorder_Create.groovy`:

```groovy
import groovy.xml.XmlSlurper
```

*(Note: In Groovy 2.4/3.0, `groovy.xml.XmlSlurper` is the canonical class package).*

---

### Corrected Script

Update **`script/GLOVIA_Conditioncheck_Salesorder_Create.groovy`**:

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

*(Optional best practice: If the incoming XML payload contains explicit namespace definitions that `XmlSlurper` should ignore for easier node traversal, you can use `new XmlSlurper(false, false).parseText(body)`).*
