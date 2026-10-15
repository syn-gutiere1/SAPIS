import com.sap.gateway.ip.core.customdev.util.Message

// `XmlSlurper` is not in the implicit imports of the Groovy 4 runtime: on Groovy 3
// it resolved through `groovy.util`, which is no longer auto-imported. Without this
// import the script does not compile at all on Groovy 4 and the whole step raises.
// SAP documents the same fix for upgrade readiness (import groovy.xml.XmlSlurper).
import groovy.xml.XmlSlurper

def Message processData(Message message) {
    def body = message.getBody(String)
    def xml = new XmlSlurper().parseText(body)

    // Navigate by LOCAL NAME, not by namespace prefix.
    //
    // `XmlSlurper` discards the prefixes when it exposes element names: with the
    // payload bound to `SOAP:`, `ns1:` and `ns2:`, `xml.name()` returns `Envelope`,
    // `notifications`, `sObject` - never `SOAP:Envelope` or `ns1:notifications`.
    // The previous `xml.'ns1:notifications'.'ns1:Notification'...` therefore matched
    // nothing, the order code came back as `""`, and `validCodes.contains("")` is
    // always false: every order - valid or not - was marked invalid.
    def orderCode = xml.Body.notifications.Notification.sObject.giic_OrderTypeCode__c.text()
    def validCodes = ['YORB', 'YICB', 'POR', 'MNZ', 'PLM']

    if (validCodes.contains(orderCode)) {
        message.setHeader("OrderCodeValid", "true")
    } else {
        message.setHeader("OrderCodeValid", "false")
    }

    return message
}
