package com.sap.gateway.ip.core.customdev.util

/**
 * Stub of the SAP Cloud Integration MessageLog.
 *
 * Records what the script would write to the tenant log, so that the test can
 * verify the behavior instead of only confirming that the script did not throw
 * an exception.
 */
class MessageLog {

    private final List<Map<String, String>> customHeaderProperties = []
    private final List<String> attachments = []
    private final Map<String, String> attachmentContent = [:]
    private final Map<String, String> attachmentContentType = [:]
    private String payload

    /** Overload used by the scripts: addCustomHeaderProperty(String, String). */
    void addCustomHeaderProperty(String name, String value) {
        customHeaderProperties << [name: name, value: value]
    }

    /** Some versions accept a map of properties. */
    void addCustomHeaderProperty(Map<String, String> properties) {
        properties.each { key, value -> addCustomHeaderProperty(key as String, value as String) }
    }

    void addAttachment(String name, String contentType, byte[] content) {
        attachments << name
        attachmentContentType[name] = contentType
        attachmentContent[name] = content == null ? null : new String(content, 'UTF-8')
    }

    /**
     * The overload the real scripts use to attach a string.
     *
     * A real iFlow script calls `addAttachmentAsString("Payload", body, "text/plain")`.
     * Without this method that script could not run under the harness at all, so the
     * attachment content is recorded and exposed for assertions.
     */
    void addAttachmentAsString(String name, String content, String contentType) {
        attachments << name
        attachmentContentType[name] = contentType
        attachmentContent[name] = content
    }

    void setPayload(String payload) {
        this.payload = payload
    }

    String getPayload() {
        return payload
    }

    List<Map<String, String>> getCustomHeaderProperties() {
        return customHeaderProperties
    }

    List<String> getAttachments() {
        return attachments
    }

    /** Content of each attachment, keyed by name. */
    Map<String, String> getAttachmentContent() {
        return attachmentContent
    }

    /** Declared content type of each attachment, keyed by name. */
    Map<String, String> getAttachmentContentType() {
        return attachmentContentType
    }

    /** name->value map, to make the check in the assertions easier. */
    Map<String, String> getCustomHeaderPropertyMap() {
        Map<String, String> result = [:]
        customHeaderProperties.each { result[it.name] = it.value }
        return result
    }
}
