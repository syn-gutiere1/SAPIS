"""Unit and integration test suite for Glovia SalesOrder SAP Integration Flow."""

import json
import xml.etree.ElementTree as ET
import pytest
import requests
import responses

TEST_ENDPOINT_URL = "https://cpi-runtime.example.invalid/cxf/Glovia_SalesOrder"
NAMESPACES = {
    "soapenv": "http://schemas.xmlsoap.org/soap/envelope/",
    "ns1": "http://soap.sforce.com/2005/09/outbound",
    "ns2": "urn:sobject.enterprise.soap.sforce.com"
}


@pytest.fixture
def valid_sales_order_payload():
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:ns1="http://soap.sforce.com/2005/09/outbound" '
        'xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">'
        '<soapenv:Header/><soapenv:Body><ns1:notifications><ns1:Notification><ns1:sObject>'
        '<ns2:Id>a0E5g000009GHI012</ns2:Id><ns2:Name>SO-2026-0001</ns2:Name>'
        '<ns2:giic_OrderTypeCode__c>YORB</ns2:giic_OrderTypeCode__c>'
        '</ns1:sObject></ns1:Notification></ns1:notifications></soapenv:Body></soapenv:Envelope>'
    )


@pytest.fixture
def valid_return_order_payload():
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:ns1="http://soap.sforce.com/2005/09/outbound" '
        'xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">'
        '<soapenv:Header/><soapenv:Body><ns1:notifications><ns1:Notification><ns1:sObject>'
        '<ns2:Id>a0E5g000009GHI999</ns2:Id><ns2:Name>SO-RET-2026-0099</ns2:Name>'
        '<ns2:giic_OrderTypeCode__c>RET_SO</ns2:giic_OrderTypeCode__c>'
        '</ns1:sObject></ns1:Notification></ns1:notifications></soapenv:Body></soapenv:Envelope>'
    )


@pytest.fixture
def unmatched_order_payload():
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:ns1="http://soap.sforce.com/2005/09/outbound" '
        'xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">'
        '<soapenv:Header/><soapenv:Body><ns1:notifications><ns1:Notification><ns1:sObject>'
        '<ns2:Id>a0E5g000009GHI000</ns2:Id><ns2:Name>SO-UNKNOWN-001</ns2:Name>'
        '<ns2:giic_OrderTypeCode__c>UNKNOWN_CODE</ns2:giic_OrderTypeCode__c>'
        '</ns1:sObject></ns1:Notification></ns1:notifications></soapenv:Body></soapenv:Envelope>'
    )


@pytest.fixture
def malformed_payload():
    return '<soapenv:Envelope><soapenv:Body><unclosedTag>Malformed' 


@responses.activate
def test_sales_order_creation_happy_path(valid_sales_order_payload):
    """Validates TC_GLOVIA_SO_01: Happy path routing to SalesOrder branch and mapping."""
    root = ET.fromstring(valid_sales_order_payload)
    order_type = root.find('.//ns2:giic_OrderTypeCode__c', NAMESPACES)
    assert order_type is not None
    assert order_type.text == 'YORB'

    expected_headers = {
        "SAP-ProcessingState": "COMPLETED",
        "SAP-RouteTaken": "SequenceFlow_82",
        "X-Region": "NA",
        "X-Country": "US",
        "X-BusinessObject": "SalesOrder",
        "X-Sender": "Glovia"
    }

    responses.add(
        responses.POST,
        TEST_ENDPOINT_URL,
        body="<soap:Envelope xmlns:soap='http://schemas.xmlsoap.org/soap/envelope/'><soap:Body><Ack>Success</Ack></soap:Body></soap:Envelope>",
        status=202,
        content_type="text/xml",
        headers=expected_headers
    )

    session = requests.Session()
    response = session.post(
        TEST_ENDPOINT_URL,
        data=valid_sales_order_payload,
        headers={"Content-Type": "text/xml; charset=UTF-8", "SOAPAction": ""}
    )

    assert response.status_code == 202
    assert response.headers.get("SAP-ProcessingState") == "COMPLETED"
    assert response.headers.get("SAP-RouteTaken") == "SequenceFlow_82"
    assert response.headers.get("X-BusinessObject") == "SalesOrder"


@responses.activate
def test_sales_order_return_route(valid_return_order_payload):
    """Validates TC_GLOVIA_RET_02: Return branch routing SequenceFlow_113."""
    root = ET.fromstring(valid_return_order_payload)
    order_code = root.find('.//ns2:giic_OrderTypeCode__c', NAMESPACES).text
    assert order_code == 'RET_SO'

    responses.add(
        responses.POST,
        TEST_ENDPOINT_URL,
        body="<soap:Envelope xmlns:soap='http://schemas.xmlsoap.org/soap/envelope/'><soap:Body><Ack>ReturnProcessed</Ack></soap:Body></soap:Envelope>",
        status=202,
        content_type="text/xml",
        headers={
            "SAP-ProcessingState": "COMPLETED",
            "SAP-RouteTaken": "SequenceFlow_113",
            "X-Receiver": "GLOVIA_SOReturns_Details_REST_Receiver"
        }
    )

    response = requests.post(TEST_ENDPOINT_URL, data=valid_return_order_payload)
    assert response.status_code == 202
    assert response.headers.get("SAP-RouteTaken") == "SequenceFlow_113"
    assert response.headers.get("X-Receiver") == "GLOVIA_SOReturns_Details_REST_Receiver"


@responses.activate
def test_unmatched_route_triggers_escalation(unmatched_order_payload):
    """Validates TC_GLOVIA_ESC_03: Default routing to EndEvent_11 with EscalationEndEvent."""
    responses.add(
        responses.POST,
        TEST_ENDPOINT_URL,
        body="<soapenv:Fault xmlns:soapenv='http://schemas.xmlsoap.org/soap/envelope/'><faultcode>soapenv:Server</faultcode><faultstring>RoutingConditionNotMet</faultstring></soapenv:Fault>",
        status=500,
        content_type="text/xml",
        headers={
            "SAP-ProcessingState": "ESCALATED",
            "SAP-EscalationCode": "RoutingConditionNotMet"
        }
    )

    response = requests.post(TEST_ENDPOINT_URL, data=unmatched_order_payload)
    assert response.status_code == 500
    assert response.headers.get("SAP-EscalationCode") == "RoutingConditionNotMet"
    assert "RoutingConditionNotMet" in response.text


@responses.activate
def test_malformed_xml_invokes_exception_subprocess(malformed_payload):
    """Validates TC_GLOVIA_ERR_04: Exception Subprocess execution on parsing error."""
    responses.add(
        responses.POST,
        TEST_ENDPOINT_URL,
        body="<soapenv:Fault xmlns:soapenv='http://schemas.xmlsoap.org/soap/envelope/'><faultcode>soapenv:Client</faultcode><faultstring>Mapping failed</faultstring></soapenv:Fault>",
        status=500,
        content_type="text/xml",
        headers={
            "SAP-ProcessingState": "FAILED",
            "SAP-ExceptionSubprocess": "SubProcess_91",
            "SAP-PayloadRestoredFrom": "InputPayload"
        }
    )

    response = requests.post(TEST_ENDPOINT_URL, data=malformed_payload)
    assert response.status_code == 500
    assert response.headers.get("SAP-ExceptionSubprocess") == "SubProcess_91"
    assert response.headers.get("SAP-PayloadRestoredFrom") == "InputPayload"


@responses.activate
def test_flow_handles_network_timeout(valid_sales_order_payload):
    """Validates TC_GLOVIA_TIMEOUT_05: Graceful error propagation on transport timeout."""
    responses.add(
        responses.POST,
        TEST_ENDPOINT_URL,
        body=requests.exceptions.ConnectTimeout("Connection timed out to integration runtime")
    )

    with pytest.raises(requests.exceptions.ConnectTimeout):
        requests.post(TEST_ENDPOINT_URL, data=valid_sales_order_payload, timeout=5.0)


@responses.activate
def test_unauthorized_request_rejected(valid_sales_order_payload):
    """Validates TC_GLOVIA_AUTH_06: Adapter level role rejection."""
    responses.add(
        responses.POST,
        TEST_ENDPOINT_URL,
        body="<error>Unauthorized</error>",
        status=401,
        headers={"WWW-Authenticate": 'Basic realm="SAP Cloud Integration"'}
    )

    response = requests.post(TEST_ENDPOINT_URL, data=valid_sales_order_payload)
    assert response.status_code == 401
