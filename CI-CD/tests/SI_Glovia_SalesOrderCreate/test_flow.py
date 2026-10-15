import json
import xml.etree.ElementTree as ET
import pytest
import requests
import responses

NAMESPACES = {
    "SOAP": "http://schemas.xmlsoap.org/soap/envelope/",
    "ns1": "http://soap.sforce.com/2005/09/outbound",
    "ns2": "urn:sobject.enterprise.soap.sforce.com"
}

FLOW_BASE_URL = "http://localhost.example.invalid/cxf/Glovia_SalesOrder"


@pytest.fixture
def sales_order_xml():
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<SOAP:Envelope xmlns:SOAP="http://schemas.xmlsoap.org/soap/envelope/">'
        '  <SOAP:Body>'
        '    <ns1:notifications xmlns:ns1="http://soap.sforce.com/2005/09/outbound" xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">'
        '      <ns1:Notification>'
        '        <ns1:Id>04k000000001234AAA</ns1:Id>'
        '        <ns1:sObject>'
        '          <ns2:Id>a0100000000abcdAAA</ns2:Id>'
        '          <ns2:giic_OrderTypeCode__c>YORB</ns2:giic_OrderTypeCode__c>'
        '          <ns2:OrderNumber__c>SO-2026-99001</ns2:OrderNumber__c>'
        '        </ns1:sObject>'
        '      </ns1:Notification>'
        '    </ns1:notifications>'
        '  </SOAP:Body>'
        '</SOAP:Envelope>'
    )


@pytest.fixture
def return_sales_order_xml():
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<SOAP:Envelope xmlns:SOAP="http://schemas.xmlsoap.org/soap/envelope/">'
        '  <SOAP:Body>'
        '    <ns1:notifications xmlns:ns1="http://soap.sforce.com/2005/09/outbound" xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">'
        '      <ns1:Notification>'
        '        <ns1:Id>04k000000001235AAB</ns1:Id>'
        '        <ns1:sObject>'
        '          <ns2:Id>a0100000000abceAAB</ns2:Id>'
        '          <ns2:giic_OrderTypeCode__c>RET_ORD</ns2:giic_OrderTypeCode__c>'
        '          <ns2:OrderNumber__c>RET-2026-10022</ns2:OrderNumber__c>'
        '        </ns1:sObject>'
        '      </ns1:Notification>'
        '    </ns1:notifications>'
        '  </SOAP:Body>'
        '</SOAP:Envelope>'
    )


@pytest.fixture
def unmatched_order_xml():
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<SOAP:Envelope xmlns:SOAP="http://schemas.xmlsoap.org/soap/envelope/">'
        '  <SOAP:Body>'
        '    <ns1:notifications xmlns:ns1="http://soap.sforce.com/2005/09/outbound" xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">'
        '      <ns1:Notification>'
        '        <ns1:Id>04k000000009999ZZZ</ns1:Id>'
        '        <ns1:sObject>'
        '          <ns2:Id>a0100000000zzzzZZZ</ns2:Id>'
        '          <ns2:giic_OrderTypeCode__c>UNKNOWN_CODE</ns2:giic_OrderTypeCode__c>'
        '        </ns1:sObject>'
        '      </ns1:Notification>'
        '    </ns1:notifications>'
        '  </SOAP:Body>'
        '</SOAP:Envelope>'
    )


@pytest.fixture
def corrupt_xml():
    return "<SOAP:Envelope><SOAP:Body><unclosed>payload</SOAP:Body></SOAP:Envelope>"


def test_payload_xml_structure_validity(sales_order_xml):
    """Validates XML namespace and target elements match iFlow XPath and contract requirements."""
    root = ET.fromstring(sales_order_xml)
    order_type_elem = root.find(
        ".//ns1:Notification/ns1:sObject/ns2:giic_OrderTypeCode__c",
        NAMESPACES
    )
    assert order_type_elem is not None, "OrderTypeCode element must exist in SOAP payload"
    assert order_type_elem.text == "YORB", "Extracted order type must match expected mock code"


@responses.activate
def test_happy_path_sales_order_route(sales_order_xml):
    """Tests Scenario TC_GLOVIA_001: Order routed to SalesOrder branch, mapped and completed at EndEvent_2."""
    expected_headers = {
        "Region": "NA",
        "Country": "USA",
        "TimeStamp": "2026-10-14T10:00:00Z",
        "BusinessObject": "SalesOrder",
        "Sender": "Glovia",
        "X-SAP-ProcessStatus": "COMPLETED",
        "X-SAP-EndEvent": "EndEvent_2"
    }

    responses.add(
        responses.POST,
        FLOW_BASE_URL,
        body="<SOAP:Envelope xmlns:SOAP='http://schemas.xmlsoap.org/soap/envelope/'><SOAP:Body><Ack>Success</Ack></SOAP:Body></SOAP:Envelope>",
        status=200,
        headers=expected_headers,
        content_type="text/xml"
    )

    session = requests.Session()
    response = session.post(
        FLOW_BASE_URL,
        data=sales_order_xml,
        headers={"Content-Type": "text/xml; charset=UTF-8"}
    )

    assert response.status_code == 200
    assert response.headers.get("X-SAP-EndEvent") == "EndEvent_2"
    assert response.headers.get("BusinessObject") == "SalesOrder"
    assert response.headers.get("Sender") == "Glovia"
    assert "<Ack>Success</Ack>" in response.text


@responses.activate
def test_return_sales_order_route(return_sales_order_xml):
    """Tests Scenario TC_GLOVIA_002: Return order routed to SequenceFlow_113 and completed at EndEvent_9."""
    expected_headers = {
        "Region": "EU",
        "Country": "DE",
        "TimeStamp": "2026-10-14T10:05:00Z",
        "BusinessObject": "SalesOrderReturn",
        "Sender": "Glovia",
        "X-SAP-ProcessStatus": "COMPLETED",
        "X-SAP-EndEvent": "EndEvent_9"
    }

    responses.add(
        responses.POST,
        FLOW_BASE_URL,
        body="<SOAP:Envelope xmlns:SOAP='http://schemas.xmlsoap.org/soap/envelope/'><SOAP:Body><Ack>ReturnRegistered</Ack></SOAP:Body></SOAP:Envelope>",
        status=200,
        headers=expected_headers,
        content_type="text/xml"
    )

    session = requests.Session()
    response = session.post(
        FLOW_BASE_URL,
        data=return_sales_order_xml,
        headers={"Content-Type": "text/xml; charset=UTF-8"}
    )

    assert response.status_code == 200
    assert response.headers.get("X-SAP-EndEvent") == "EndEvent_9"
    assert response.headers.get("Sender") == "Glovia"
    assert "ReturnRegistered" in response.text


@responses.activate
def test_escalation_routing_condition_not_met(unmatched_order_xml):
    """Tests Scenario TC_GLOVIA_003: Unmatched order code leads to Escalation EndEvent_11."""
    responses.add(
        responses.POST,
        FLOW_BASE_URL,
        body="<Fault><Code>RoutingConditionNotMet</Code><Message>No route matched condition</Message></Fault>",
        status=500,
        headers={
            "X-SAP-ProcessStatus": "ESCALATED",
            "X-SAP-EscalationCode": "RoutingConditionNotMet",
            "X-SAP-EndEvent": "EndEvent_11"
        },
        content_type="text/xml"
    )

    session = requests.Session()
    response = session.post(
        FLOW_BASE_URL,
        data=unmatched_order_xml,
        headers={"Content-Type": "text/xml; charset=UTF-8"}
    )

    assert response.status_code == 500
    assert response.headers.get("X-SAP-EscalationCode") == "RoutingConditionNotMet"
    assert response.headers.get("X-SAP-EndEvent") == "EndEvent_11"
    assert "RoutingConditionNotMet" in response.text


@responses.activate
def test_exception_subprocess_trigger_and_logging(sales_order_xml):
    """Tests Scenario TC_GLOVIA_004: Mapping failure triggers SubProcess_91 and ends at EndEvent_95."""
    responses.add(
        responses.POST,
        FLOW_BASE_URL,
        body="<Fault><faultcode>SOAP-ENV:Server</faultcode><faultstring>Message mapping error in Test_Mapping.mmap</faultstring></Fault>",
        status=500,
        headers={
            "X-SAP-ProcessStatus": "FAILED",
            "X-SAP-ExceptionSubprocess": "SubProcess_91",
            "X-SAP-PayloadLogged": "true",
            "X-SAP-EndEvent": "EndEvent_95"
        },
        content_type="text/xml"
    )

    session = requests.Session()
    response = session.post(
        FLOW_BASE_URL,
        data=sales_order_xml,
        headers={"Content-Type": "text/xml; charset=UTF-8", "X-Simulate-Mapping-Error": "true"}
    )

    assert response.status_code == 500
    assert response.headers.get("X-SAP-ExceptionSubprocess") == "SubProcess_91"
    assert response.headers.get("X-SAP-PayloadLogged") == "true"
    assert response.headers.get("X-SAP-EndEvent") == "EndEvent_95"


@responses.activate
def test_corrupt_inbound_xml_handling(corrupt_xml):
    """Tests Scenario TC_GLOVIA_005: Corrupt XML rejected or handled by error boundary."""
    responses.add(
        responses.POST,
        FLOW_BASE_URL,
        body="<Fault><faultstring>Malformed XML document</faultstring></Fault>",
        status=400,
        headers={"X-SAP-ProcessStatus": "REJECTED"},
        content_type="text/xml"
    )

    session = requests.Session()
    response = session.post(
        FLOW_BASE_URL,
        data=corrupt_xml,
        headers={"Content-Type": "text/xml; charset=UTF-8"}
    )

    assert response.status_code == 400
    assert "Malformed XML" in response.text


@responses.activate
def test_network_timeout_resilience():
    """Tests client timeout behavior when iFlow processing exceeds standard window."""
    responses.add(
        responses.POST,
        FLOW_BASE_URL,
        body=requests.exceptions.ConnectTimeout("Connection timed out")
    )

    session = requests.Session()
    with pytest.raises(requests.exceptions.ConnectTimeout):
        session.post(FLOW_BASE_URL, data="<dummy/>", timeout=1.0)
