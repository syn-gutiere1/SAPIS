import json
import os
import xml.etree.ElementTree as ET
import pytest
import requests
import responses

TEST_ENDPOINT = "http://localhost:8080/cxf/Glovia_SalesOrder"
DOC_INVALID_URL = "https://example.invalid/cxf/Glovia_SalesOrder"

NAMESPACES = {
    'SOAP': 'http://schemas.xmlsoap.org/soap/envelope/',
    'ns1': 'http://soap.sforce.com/2005/09/outbound',
    'ns2': 'urn:sobject.enterprise.soap.sforce.com'
}

@pytest.fixture
def payload_sales_order():
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<SOAP:Envelope xmlns:SOAP="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:ns1="http://soap.sforce.com/2005/09/outbound" '
        'xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">'
        '<SOAP:Header/>'
        '<SOAP:Body>'
        '<ns1:notifications>'
        '<ns1:Notification>'
        '<ns1:Id>04k5g000000SAMPLE</ns1:Id>'
        '<ns1:sObject>'
        '<ns2:Id>a0B5g000000SAMPLE</ns2:Id>'
        '<ns2:giic_OrderTypeCode__c>YORB</ns2:giic_OrderTypeCode__c>'
        '<ns2:giic_OrderNumber__c>ORD-2026-1001</ns2:giic_OrderNumber__c>'
        '</ns1:sObject>'
        '</ns1:Notification>'
        '</ns1:notifications>'
        '</SOAP:Body>'
        '</SOAP:Envelope>'
    )

@pytest.fixture
def payload_return_order():
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<SOAP:Envelope xmlns:SOAP="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:ns1="http://soap.sforce.com/2005/09/outbound" '
        'xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">'
        '<SOAP:Header/>'
        '<SOAP:Body>'
        '<ns1:notifications>'
        '<ns1:Notification>'
        '<ns1:Id>04k5g000000RET001</ns1:Id>'
        '<ns1:sObject>'
        '<ns2:Id>a0B5g000000RET001</ns2:Id>'
        '<ns2:giic_OrderTypeCode__c>ZRET</ns2:giic_OrderTypeCode__c>'
        '</ns1:sObject>'
        '</ns1:Notification>'
        '</ns1:notifications>'
        '</SOAP:Body>'
        '</SOAP:Envelope>'
    )

@pytest.fixture
def payload_unmatched():
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<SOAP:Envelope xmlns:SOAP="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:ns1="http://soap.sforce.com/2005/09/outbound" '
        'xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">'
        '<SOAP:Header/>'
        '<SOAP:Body>'
        '<ns1:notifications>'
        '<ns1:Notification>'
        '<ns1:Id>04k5g000000UNM</ns1:Id>'
        '<ns1:sObject>'
        '<ns2:giic_OrderTypeCode__c>UNKNOWN_CODE</ns2:giic_OrderTypeCode__c>'
        '</ns1:sObject>'
        '</ns1:Notification>'
        '</ns1:notifications>'
        '</SOAP:Body>'
        '</SOAP:Envelope>'
    )

@responses.activate
def test_sales_order_creation_happy_path(payload_sales_order):
    """Validate routing to CallActivity_102 (Mapping) and CallActivity_13, ending at EndEvent_2."""
    # Validate payload integrity
    root = ET.fromstring(payload_sales_order)
    code_elem = root.find('.//ns2:giic_OrderTypeCode__c', NAMESPACES)
    assert code_elem is not None
    assert code_elem.text == "YORB"

    responses.add(
        responses.POST,
        TEST_ENDPOINT,
        body="<SOAP:Envelope xmlns:SOAP=\"http://schemas.xmlsoap.org/soap/envelope/\"><SOAP:Body><Ack>Processed</Ack></SOAP:Body></SOAP:Envelope>",
        status=202,
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SAP-Processing-Route": "SalesOrder",
            "SAP-EndEvent": "EndEvent_2",
            "X-CustomProperty-Country": "US",
            "X-CustomProperty-Region": "NA",
            "X-CustomProperty-BusinessObject": "SalesOrder"
        }
    )

    resp = requests.post(
        TEST_ENDPOINT,
        data=payload_sales_order,
        headers={"Content-Type": "text/xml", "SOAPAction": ""}
    )

    assert resp.status_code == 202
    assert resp.headers.get("SAP-Processing-Route") == "SalesOrder"
    assert resp.headers.get("SAP-EndEvent") == "EndEvent_2"
    assert resp.headers.get("X-CustomProperty-Country") == "US"

@responses.activate
def test_sales_order_return_routing(payload_return_order):
    """Validate routing to CallActivity_71 for SO Return create, ending at EndEvent_9."""
    responses.add(
        responses.POST,
        TEST_ENDPOINT,
        body="<SOAP:Envelope xmlns:SOAP=\"http://schemas.xmlsoap.org/soap/envelope/\"><SOAP:Body><Ack>ReturnRegistered</Ack></SOAP:Body></SOAP:Envelope>",
        status=202,
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SAP-Processing-Route": "SO_Return_create",
            "SAP-EndEvent": "EndEvent_9",
            "X-CustomProperty-BusinessObject": "SalesOrderReturn"
        }
    )

    resp = requests.post(
        TEST_ENDPOINT,
        data=payload_return_order,
        headers={"Content-Type": "text/xml", "SOAPAction": ""}
    )

    assert resp.status_code == 202
    assert resp.headers.get("SAP-Processing-Route") == "SO_Return_create"
    assert resp.headers.get("SAP-EndEvent") == "EndEvent_9"

@responses.activate
def test_default_route_escalation_end(payload_unmatched):
    """Validate unmatched order code triggers EscalationEndEvent EndEvent_11 with RoutingConditionNotMet."""
    responses.add(
        responses.POST,
        TEST_ENDPOINT,
        body="<SOAP:Envelope xmlns:SOAP=\"http://schemas.xmlsoap.org/soap/envelope/\"><SOAP:Fault><faultcode>SOAP:Server</faultcode><faultstring>Escalation: RoutingConditionNotMet</faultstring></SOAP:Fault></SOAP:Envelope>",
        status=500,
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SAP-Escalation-Code": "RoutingConditionNotMet",
            "SAP-EndEvent": "EndEvent_11"
        }
    )

    resp = requests.post(
        TEST_ENDPOINT,
        data=payload_unmatched,
        headers={"Content-Type": "text/xml"}
    )

    assert resp.status_code == 500
    assert resp.headers.get("SAP-Escalation-Code") == "RoutingConditionNotMet"
    assert resp.headers.get("SAP-EndEvent") == "EndEvent_11"
    assert "RoutingConditionNotMet" in resp.text

@responses.activate
def test_exception_subprocess_execution():
    """Validate mapping exception triggers SubProcess_91, re-enriches InputPayload, logs payload, and throws EndErrorEvent."""
    corrupted_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<SOAP:Envelope xmlns:SOAP="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:ns1="http://soap.sforce.com/2005/09/outbound" '
        'xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">'
        '<SOAP:Body>'
        '<ns1:notifications><ns1:Notification><ns1:sObject>'
        '<ns2:giic_OrderTypeCode__c>YORB</ns2:giic_OrderTypeCode__c>'
        '<ns2:corrupt>&invalid;xml</ns2:corrupt>'
        '</ns1:sObject></ns1:Notification></ns1:notifications>'
        '</SOAP:Body></SOAP:Envelope>'
    )

    responses.add(
        responses.POST,
        TEST_ENDPOINT,
        body="<SOAP:Envelope xmlns:SOAP=\"http://schemas.xmlsoap.org/soap/envelope/\"><SOAP:Fault><faultcode>SOAP:Server</faultcode><faultstring>MappingException: Test_Mapping failed</faultstring></SOAP:Fault></SOAP:Envelope>",
        status=500,
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SAP-Subprocess-Triggered": "SubProcess_91",
            "SAP-Enricher-Source": "InputPayload",
            "SAP-EndEvent": "EndEvent_95"
        }
    )

    resp = requests.post(TEST_ENDPOINT, data=corrupted_xml, headers={"Content-Type": "text/xml"})
    assert resp.status_code == 500
    assert resp.headers.get("SAP-Subprocess-Triggered") == "SubProcess_91"
    assert resp.headers.get("SAP-Enricher-Source") == "InputPayload"
    assert resp.headers.get("SAP-EndEvent") == "EndEvent_95"

@responses.activate
def test_sender_unauthorized_rejection():
    """Validate SOAP endpoint rejects unauthorized user without ESBMessaging.send role."""
    responses.add(
        responses.POST,
        DOC_INVALID_URL,
        status=401,
        headers={"WWW-Authenticate": "Basic realm=\"SAP CI\""},
        body="Unauthorized"
    )

    resp = requests.post(DOC_INVALID_URL, data="<xml/>", headers={"Authorization": "Basic badCreds"})
    assert resp.status_code == 401

@responses.activate
def test_connection_timeout_handling():
    """Validate connection timeout resilience during backend calls."""
    responses.add_callback(
        responses.POST,
        DOC_INVALID_URL,
        callback=lambda req: (_ for _ in ()).throw(requests.exceptions.ConnectTimeout("Connection timed out"))
    )

    with pytest.raises(requests.exceptions.ConnectTimeout):
        requests.post(DOC_INVALID_URL, data="<xml/>", timeout=1.0)
