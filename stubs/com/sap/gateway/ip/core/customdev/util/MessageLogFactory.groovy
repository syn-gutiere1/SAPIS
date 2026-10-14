package com.sap.gateway.ip.core.customdev.util

/**
 * Stub of the SAP Cloud Integration MessageLogFactory.
 *
 * The tenant runtime injects `messageLogFactory` into the script binding. Real
 * scripts use `messageLogFactory.getMessageLog(message)` to write to the message
 * log. Without this stub, any script that uses logging fails with
 * MissingPropertyException: No such property: messageLogFactory.
 *
 * The harness injects an instance of this class into the binding before running
 * the script, and then reads the MessageLog to verify the behavior.
 */
class MessageLogFactory {

    private MessageLog messageLog = new MessageLog()

    /** Always returns the same instance, so the test can inspect the log. */
    MessageLog getMessageLog(Message message) {
        return messageLog
    }

    /** Simulates the case where there is no active log in the tenant. */
    void disable() {
        this.messageLog = null
    }

    MessageLog getRecordedLog() {
        return messageLog
    }
}
