import com.sap.gateway.ip.core.customdev.util.Message

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
