import json
import xml.etree.ElementTree as ET
import pytest
import requests
import responses

BASE_URL = "https://cpi.example.invalid/cxf/Glovia_SalesOrder"

# Namespace mappings from iFlow definitions
NAMESPACES = {
    "SOAP": "http://schemas.xmlsoap.org/soap/envelope/",
    "ns1": "http://soap.sforce.com/2005/09/outbound",
    "ns2": "urn:sobject.enterprise.soap.sforce.com"
}

EXTERNALIZED_CONFIG = {
    "Region": "NA",
    "Country": "US",
    "TimeStamp": "2026-10-14T08:30:00Z",
    "BusinessObject": "SalesOrder",
    "Sender": "Glovia",
    "Glovia_Route_SalesOrder": "//ns1:notifications/ns1:Notification/ns1:sObject[ns2:giic_OrderTypeCode__c='YORB']",
    "Glovia_Route2_ReturnSalesOrder": "//ns1:notifications/ns1:Notification/ns1:sObject[ns2:giic_OrderTypeCode__c='POR']"
}

@pytest.fixture
def payload_sales_order():
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<SOAP:Envelope xmlns:SOAP=\"http://schemas.xmlsoap.org/soap/envelope/\" "
        "xmlns:ns1=\"http://soap.sforce.com/2005/09/outbound\" "
        "xmlns:ns2=\"urn:sobject.enterprise.soap.sforce.com\">\n"
        "  <SOAP:Header/>\n"
        "  <SOAP:Body>\n"
        "    <ns1:notifications>\n"
        "      <ns1:Notification>\n"
        "        <ns1:Id>04k000000000001AAA</ns1:Id>\n"
        "        <ns1:sObject>\n"
        "          <ns2:Id>a0B000000000001EAA</ns2:Id>\n"
        "          <ns2:giic_OrderTypeCode__c>YORB</ns2:giic_OrderTypeCode__c>\n"
        "          <ns2:giic_OrderNumber__c>ORD-99201</ns2:giic_OrderNumber__c>\n"
        "        </ns1:sObject>\n"
        "      </ns1:Notification>\n"
        "    </ns1:notifications>\n"
        "  </SOAP:Body>\n"
        "</SOAP:Envelope>"
    )

@pytest.fixture
def payload_return_order():
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<SOAP:Envelope xmlns:SOAP=\"http://schemas.xmlsoap.org/soap/envelope/\" "
        "xmlns:ns1=\"http://soap.sforce.com/2005/09/outbound\" "
        "xmlns:ns2=\"urn:sobject.enterprise.soap.sforce.com\">\n"
        "  <SOAP:Header/>\n"
        "  <SOAP:Body>\n"
        "    <ns1:notifications>\n"
        "      <ns1:Notification>\n"
        "        <ns1:Id>04k000000000002AAA</ns1:Id>\n"
        "        <ns1:sObject>\n"
        "          <ns2:Id>a0B000000000002EAA</ns2:Id>\n"
        "          <ns2:giic_OrderTypeCode__c>POR</ns2:giic_OrderTypeCode__c>\n"
        "          <ns2:giic_OrderNumber__c>RET-44102</ns2:giic_OrderNumber__c>\n"
        "        </ns1:sObject>\n"
        "      </ns1:Notification>\n"
        "    </ns1:notifications>\n"
        "  </SOAP:Body>\n"
        "</SOAP:Envelope>"
    )

@pytest.fixture
def payload_invalid_routing():
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<SOAP:Envelope xmlns:SOAP=\"http://schemas.xmlsoap.org/soap/envelope/\" "
        "xmlns:ns1=\"http://soap.sforce.com/2005/09/outbound\" "
        "xmlns:ns2=\"urn:sobject.enterprise.soap.sforce.com\">\n"
        "  <SOAP:Header/>\n"
        "  <SOAP:Body>\n"
        "    <ns1:notifications>\n"
        "      <ns1:Notification>\n"
        "        <ns1:Id>04k000000000003AAA</ns1:Id>\n"
        "        <ns1:sObject>\n"
        "          <ns2:Id>a0B000000000003EAA</ns2:Id>\n"
        "          <ns2:giic_OrderTypeCode__c>UNKNOWN_ROUTE</ns2:giic_OrderTypeCode__c>\n"
        "        </ns1:sObject>\n"
        "      </ns1:Notification>\n"
        "    </ns1:notifications>\n"
        "  </SOAP:Body>\n"
        "</SOAP:Envelope>"
    )

def simulate_cpi_inbound_runtime(request_body, config):
    """Simulates the iFlow processing step behavior for assertions."""
    try:
        root = ET.fromstring(request_body)
    except ET.ParseError:
        # SubProcess_91 - Read payload from property and terminate with error
        return {
            "status_code": 500,
            "body": "<SOAP:Fault><faultcode>SOAP:Server</faultcode><faultstring>XML parsing error occurred</faultstring></SOAP:Fault>",
            "headers": {"Content-Type": "text/xml", "X-SAP-Integration-Status": "FAILED"},
            "properties": {"InputPayload": request_body},
            "route_executed": "SubProcess_91"
        }

    # Step CallActivity_4: Set Message Headers
    headers = {
        "Region": config["Region"],
        "Country": config["Country"],
        "TimeStamp": config["TimeStamp"],
        "BusinessObject": config["BusinessObject"],
        "Sender": config["Sender"],
        "Content-Type": "text/xml"
    }
    properties = {"InputPayload": request_body}

    # ExclusiveGateway_80 evaluation
    order_code_elem = root.find(".//ns1:Notification/ns1:sObject/ns2:giic_OrderTypeCode__c", NAMESPACES)
    order_code = order_code_elem.text if order_code_elem is not None else ""

    if order_code == "YORB":
        headers["X-Route-Path"] = "SequenceFlow_82_SalesOrder"
        headers["X-SAP-Integration-Status"] = "COMPLETED"
        return {
            "status_code": 200,
            "body": "<Ack><Status>Processed_SalesOrder</Status></Ack>",
            "headers": headers,
            "properties": properties,
            "route_executed": "SequenceFlow_82"
        }
    elif order_code == "POR":
        headers["X-Route-Path"] = "SequenceFlow_113_ReturnSalesOrder"
        headers["X-SAP-Integration-Status"] = "COMPLETED"
        return {
            "status_code": 200,
            "body": "<Ack><Status>Processed_ReturnSalesOrder</Status></Ack>",
            "headers": headers,
            "properties": properties,
            "route_executed": "SequenceFlow_113"
        }
    else:
        # Escalation End Event SequenceFlow_110
        headers["X-SAP-Integration-Status"] = "ESCALATED"
        headers["X-SAP-Escalation-Code"] = "RoutingConditionNotMet"
        return {
            "status_code": 422,
            "body": "<Fault><Code>RoutingConditionNotMet</Code><Message>No route satisfied</Message></Fault>",
            "headers": headers,
            "properties": properties,
            "route_executed": "SequenceFlow_110"
        }

@responses.activate
def test_sales_order_happy_path(payload_sales_order):
    execution = simulate_cpi_inbound_runtime(payload_sales_order, EXTERNALIZED_CONFIG)
    responses.add(
        responses.POST,
        BASE_URL,
        body=execution["body"],
        status=execution["status_code"],
        headers=execution["headers"]
    )

    session = requests.Session()
    response = session.post(BASE_URL, data=payload_sales_order, headers={"Content-Type": "text/xml"})

    assert response.status_code == 200
    assert response.headers["X-Route-Path"] == "SequenceFlow_82_SalesOrder"
    assert response.headers["Region"] == "NA"
    assert response.headers["Country"] == "US"
    assert response.headers["BusinessObject"] == "SalesOrder"
    assert "Processed_SalesOrder" in response.text
    assert execution["properties"]["InputPayload"] == payload_sales_order

@responses.activate
def test_return_sales_order_branch(payload_return_order):
    execution = simulate_cpi_inbound_runtime(payload_return_order, EXTERNALIZED_CONFIG)
    responses.add(
        responses.POST,
        BASE_URL,
        body=execution["body"],
        status=execution["status_code"],
        headers=execution["headers"]
    )

    session = requests.Session()
    response = session.post(BASE_URL, data=payload_return_order, headers={"Content-Type": "text/xml"})

    assert response.status_code == 200
    assert response.headers["X-Route-Path"] == "SequenceFlow_113_ReturnSalesOrder"
    assert response.headers["Sender"] == "Glovia"
    assert "Processed_ReturnSalesOrder" in response.text

@responses.activate
def test_unmatched_route_triggers_escalation(payload_invalid_routing):
    execution = simulate_cpi_inbound_runtime(payload_invalid_routing, EXTERNALIZED_CONFIG)
    responses.add(
        responses.POST,
        BASE_URL,
        body=execution["body"],
        status=execution["status_code"],
        headers=execution["headers"]
    )

    session = requests.Session()
    response = session.post(BASE_URL, data=payload_invalid_routing, headers={"Content-Type": "text/xml"})

    assert response.status_code == 422
    assert response.headers["X-SAP-Escalation-Code"] == "RoutingConditionNotMet"
    assert "RoutingConditionNotMet" in response.text

@responses.activate
def test_malformed_xml_activates_exception_subprocess():
    malformed_xml = "<SOAP:Envelope><broken>"
    execution = simulate_cpi_inbound_runtime(malformed_xml, EXTERNALIZED_CONFIG)
    responses.add(
        responses.POST,
        BASE_URL,
        body=execution["body"],
        status=execution["status_code"],
        headers=execution["headers"]
    )

    session = requests.Session()
    response = session.post(BASE_URL, data=malformed_xml, headers={"Content-Type": "text/xml"})

    assert response.status_code == 500
    assert response.headers["X-SAP-Integration-Status"] == "FAILED"
    assert "SOAP:Fault" in response.text
    assert execution["properties"]["InputPayload"] == malformed_xml

@responses.activate
def test_connection_timeout_and_network_error_resilience():
    responses.add(
        responses.POST,
        BASE_URL,
        body=requests.exceptions.ConnectTimeout("Connection timed out towards integration gateway")
    )

    session = requests.Session()
    with pytest.raises(requests.exceptions.ConnectTimeout):
        session.post(BASE_URL, data="<xml/>", headers={"Content-Type": "text/xml"}, timeout=3.0)
