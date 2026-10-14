package com.sap.gateway.ip.core.customdev.util

/**
 * Stub of the SAP Cloud Integration Message interface.
 *
 * Used only in local/CI testing: it allows the real Groovy scripts to run
 * outside the tenant.
 *
 * Compatibility note discovered by running the real scripts:
 * the Glovia script calls `message.getBody(String)`. Without the
 * `getBody(Class)` overload the real script fails with MissingMethodException,
 * even with the mock implementation present in the harness. The original stub
 * did not have it, which is why TestHarness was never able to run real Groovy.
 */
interface Message {
    Object getBody()
    Object getBody(Class type)
    void setBody(Object body)

    Map<String, Object> getHeaders()
    void setHeaders(Map<String, Object> headers)
    Object getHeader(String key)
    Object getHeader(String key, String defaultValue)
    void setHeader(String key, Object value)

    Map<String, Object> getProperties()
    void setProperties(Map<String, Object> properties)
    Object getProperty(String key)
    Object getProperty(String key, String defaultValue)
    void setProperty(String key, Object value)

    Object getExchangeProperty(String key)
    Object getExchangeProperty(String key, String defaultValue)
    void setExchangeProperty(String key, Object value)
}
