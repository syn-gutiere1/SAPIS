import json
import xml.etree.ElementTree as ET
import pytest
import requests
import responses

TEST_ENDPOINT = "http://localhost.example.invalid/cxf/Glovia_SalesOrder"

SOAP_NAMESPACES = {
    "SOAP": "http://schemas.xmlsoap.org/soap/envelope/",
    "ns1": "http://soap.sforce.com/2005/09/outbound",
    "ns2": "urn:sobject.enterprise.soap.sforce.com"
}

@pytest.fixture
def payload_sales_order():
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<SOAP:Envelope xmlns:SOAP="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:ns1="http://soap.sforce.com/2005/09/outbound" '
        'xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">'
        '<SOAP:Body>'
        '<ns1:notifications>'
        '<ns1:Notification>'
        '<ns1:sObject>'
        '<ns2:giic_OrderTypeCode__c>YORB</ns2:giic_OrderTypeCode__c>'
        '<ns2:OrderNumber__c>SO-902910</ns2:OrderNumber__c>'
        '</ns1:sObject>'
        '</ns1:Notification>'
        '</ns1:notifications>'
        '</SOAP:Body>'
        '</SOAP:Envelope>'
    )

@pytest.fixture
def payload_so_return():
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<SOAP:Envelope xmlns:SOAP="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:ns1="http://soap.sforce.com/2005/09/outbound" '
        'xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">'
        '<SOAP:Body>'
        '<ns1:notifications>'
        '<ns1:Notification>'
        '<ns1:sObject>'
        '<ns2:giic_OrderTypeCode__c>POR</ns2:giic_OrderTypeCode__c>'
        '<ns2:OrderNumber__c>RET-440192</ns2:OrderNumber__c>'
        '</ns1:sObject>'
        '</ns1:Notification>'
        '</ns1:notifications>'
        '</SOAP:Body>'
        '</SOAP:Envelope>'
    )

@pytest.fixture
def payload_routing_unmatched():
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<SOAP:Envelope xmlns:SOAP="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:ns1="http://soap.sforce.com/2005/09/outbound" '
        'xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">'
        '<SOAP:Body>'
        '<ns1:notifications>'
        '<ns1:Notification>'
        '<ns1:sObject>'
        '<ns2:giic_OrderTypeCode__c>UNKNOWN_CODE</ns2:giic_OrderTypeCode__c>'
        '</ns1:sObject>'
        '</ns1:Notification>'
        '</ns1:notifications>'
        '</SOAP:Body>'
        '</SOAP:Envelope>'
    )

@pytest.fixture
def payload_malformed():
    return "<SOAP:Envelope><SOAP:Body><unclosed>error</SOAP:Envelope>"

def parse_order_type(xml_string):
    root = ET.fromstring(xml_string)
    node = root.find(".//ns2:giic_OrderTypeCode__c", SOAP_NAMESPACES)
    return node.text if node is not None else None

@responses.activate
def test_sales_order_routing_happy_path(payload_sales_order):
    order_type = parse_order_type(payload_sales_order)
    assert order_type == "YORB"

    responses.add(
        responses.POST,
        TEST_ENDPOINT,
        status=202,
        headers={
            "SAP-MessageId": "MSG-SO-001",
            "X-Route-Taken": "SalesOrder",
            "Country": "US",
            "Region": "NA",
            "BusinessObject": "SalesOrder",
            "Sender": "Glovia"
        },
        body="<SOAP:Envelope xmlns:SOAP=\"http://schemas.xmlsoap.org/soap/envelope/\"><SOAP:Body><Ack>Accepted</Ack></SOAP:Body></SOAP:Envelope>"
    )

    headers = {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": ""}
    response = requests.post(TEST_ENDPOINT, data=payload_sales_order, headers=headers)

    assert response.status_code == 202
    assert response.headers["X-Route-Taken"] == "SalesOrder"
    assert response.headers["BusinessObject"] == "SalesOrder"
    assert response.headers["Region"] == "NA"
    assert "Accepted" in response.text

@responses.activate
def test_so_return_routing_path(payload_so_return):
    order_type = parse_order_type(payload_so_return)
    assert order_type == "POR"

    responses.add(
        responses.POST,
        TEST_ENDPOINT,
        status=202,
        headers={
            "SAP-MessageId": "MSG-RET-002",
            "X-Route-Taken": "SO Return create",
            "Country": "DE",
            "Region": "EMEA",
            "BusinessObject": "SalesOrderReturn",
            "Sender": "Glovia"
        },
        body="<SOAP:Envelope xmlns:SOAP=\"http://schemas.xmlsoap.org/soap/envelope/\"><SOAP:Body><Ack>Accepted</Ack></SOAP:Body></SOAP:Envelope>"
    )

    headers = {"Content-Type": "text/xml; charset=utf-8"}
    response = requests.post(TEST_ENDPOINT, data=payload_so_return, headers=headers)

    assert response.status_code == 202
    assert response.headers["X-Route-Taken"] == "SO Return create"
    assert response.headers["BusinessObject"] == "SalesOrderReturn"

@responses.activate
def test_routing_condition_not_met_escalation(payload_routing_unmatched):
    responses.add(
        responses.POST,
        TEST_ENDPOINT,
        status=500,
        headers={
            "SAP-EscalationCode": "RoutingConditionNotMet",
            "SAP-ProcessingState": "Escalated"
        },
        body="<SOAP:Fault><faultcode>SOAP:Server</faultcode><faultstring>Escalation: RoutingConditionNotMet</faultstring></SOAP:Fault>"
    )

    headers = {"Content-Type": "text/xml; charset=utf-8"}
    response = requests.post(TEST_ENDPOINT, data=payload_routing_unmatched, headers=headers)

    assert response.status_code == 500
    assert response.headers.get("SAP-EscalationCode") == "RoutingConditionNotMet"
    assert "RoutingConditionNotMet" in response.text

@responses.activate
def test_exception_subprocess_trigger(payload_malformed):
    with pytest.raises(ET.ParseError):
        ET.fromstring(payload_malformed)

    responses.add(
        responses.POST,
        TEST_ENDPOINT,
        status=500,
        headers={
            "SAP-ProcessingState": "Failed",
            "SAP-SubProcess": "SubProcess_91",
            "SAP-RestoredProperty": "InputPayload"
        },
        body="<SOAP:Fault><faultcode>SOAP:Client</faultcode><faultstring>XML parsing error occurred</faultstring></SOAP:Fault>"
    )

    response = requests.post(TEST_ENDPOINT, data=payload_malformed, headers={"Content-Type": "text/xml"})
    assert response.status_code == 500
    assert response.headers.get("SAP-SubProcess") == "SubProcess_91"
    assert response.headers.get("SAP-RestoredProperty") == "InputPayload"

@responses.activate
def test_connection_timeout_handling(payload_sales_order):
    responses.add_callback(
        responses.POST,
        TEST_ENDPOINT,
        callback=lambda request: (_ for _ in ()).throw(requests.exceptions.ConnectTimeout("Simulated Gateway Timeout"))
    )

    with pytest.raises(requests.exceptions.ConnectTimeout):
        requests.post(TEST_ENDPOINT, data=payload_sales_order, timeout=5.0)
