import json
import os
import unittest.mock
import xml.etree.ElementTree as ET
import pytest
import requests


NS = {
    "soap": "http://schemas.xmlsoap.org/soap/envelope/",
    "ns1": "http://soap.sforce.com/2005/09/outbound",
    "ns2": "urn:sobject.enterprise.soap.sforce.com"
}


class FlowExecutionEngine:
    """Simulates the SAP CPI runtime pipeline for the Glovia SalesOrder iFlow."""

    def __init__(self, endpoint_url="https://localhost.invalid/cxf/Glovia_SalesOrder"):
        self.endpoint_url = endpoint_url
        self.session = requests.Session()

    def execute(self, payload: str, headers: dict = None) -> dict:
        req_headers = {"Content-Type": "text/xml; charset=UTF-8"}
        if headers:
            req_headers.update(headers)
        return self.session.post(self.endpoint_url, data=payload, headers=req_headers)


def mock_cpi_pipeline_dispatch(request: requests.PreparedRequest):
    """Mock CPI pipeline processing logic based on BPMN specification."""
    body = request.body if isinstance(request.body, str) else request.body.decode("utf-8")
    properties = {"InputPayload": body}
    assigned_headers = {
        "Region": request.headers.get("Region", "NA"),
        "Country": request.headers.get("Country", "US"),
        "TimeStamp": request.headers.get("TimeStamp", "2026-10-14T10:00:00Z"),
        "BusinessObject": request.headers.get("BusinessObject", "SalesOrder"),
        "Sender": request.headers.get("Sender", "Glovia")
    }

    response = requests.Response()
    response.request = request

    # Validate XML parsing for inbound SOAP envelope
    try:
        root = ET.fromstring(body)
    except ET.ParseError as err:
        # SubProcess_91: Exception subprocess execution
        restored_payload = properties["InputPayload"]
        response.status_code = 500
        response.headers["SAP-Exception-SubProcess"] = "SubProcess_91"
        response.headers["SAP-Error-End"] = "EndEvent_95"
        response.headers["SAP-Payload-Restored"] = str(bool(restored_payload))
        response._content = json.dumps({
            "fault": "XMLParsingError",
            "message": str(err),
            "loggedAttachment": "Payload"
        }).encode("utf-8")
        return response

    order_code_elem = root.find(".//ns2:giic_OrderTypeCode__c", NS)
    order_type_elem = root.find(".//ns2:giic_OrderType__c", NS)
    order_code = order_code_elem.text if order_code_elem is not None else ""
    order_type = order_type_elem.text if order_type_elem is not None else ""

    # ExclusiveGateway_80 routing
    sales_order_valid_codes = {"YORB", "YICB", "POR", "MNZ", "PLM"}
    route_sales_order = order_code in sales_order_valid_codes
    route_so_return = (order_code == "RMA") or ("Return" in order_type)

    if route_sales_order:
        # Route SequenceFlow_82 -> CallActivity_102 -> CallActivity_13 -> EndEvent_2
        response.status_code = 200
        response.headers["SAP-Route-Taken"] = "SequenceFlow_82"
        response.headers["SAP-End-Event"] = "EndEvent_2"
        response.headers["SAP-Custom-Region"] = assigned_headers["Region"]
        response.headers["SAP-Custom-Country"] = assigned_headers["Country"]
        response.headers["SAP-Custom-Sender"] = assigned_headers["Sender"]
        response.headers["SAP-Mapped"] = "true"
        response._content = b"<status>SalesOrder Processed Successfully</status>"
    elif route_so_return:
        # Route SequenceFlow_113 -> CallActivity_71 -> EndEvent_9
        response.status_code = 200
        response.headers["SAP-Route-Taken"] = "SequenceFlow_113"
        response.headers["SAP-End-Event"] = "EndEvent_9"
        response.headers["SAP-Custom-Region"] = assigned_headers["Region"]
        response.headers["SAP-Custom-Country"] = assigned_headers["Country"]
        response.headers["SAP-Custom-Sender"] = assigned_headers["Sender"]
        response.headers["SAP-Mapped"] = "false"
        response._content = b"<status>ReturnSalesOrder Processed Successfully</status>"
    else:
        # Default Route SequenceFlow_110 -> EndEvent_11 (EscalationEndEvent)
        response.status_code = 409
        response.headers["SAP-Route-Taken"] = "SequenceFlow_110"
        response.headers["SAP-End-Event"] = "EndEvent_11"
        response.headers["SAP-EscalationCode"] = "RoutingConditionNotMet"
        response._content = json.dumps({
            "error": "EscalationTriggered",
            "code": "RoutingConditionNotMet"
        }).encode("utf-8")

    return response


@pytest.fixture
def flow_client():
    return FlowExecutionEngine()


@pytest.fixture
def payload_sales_order():
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<SOAP:Envelope xmlns:SOAP="http://schemas.xmlsoap.org/soap/envelope/">'
        '<SOAP:Body>'
        '<ns1:notifications xmlns:ns1="http://soap.sforce.com/2005/09/outbound" '
        'xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">'
        '<ns1:Notification>'
        '<ns1:sObject>'
        '<ns2:giic_OrderTypeCode__c>YORB</ns2:giic_OrderTypeCode__c>'
        '<ns2:giic_OrderType__c>Sales Order</ns2:giic_OrderType__c>'
        '</ns1:sObject>'
        '</ns1:Notification>'
        '</ns1:notifications>'
        '</SOAP:Body></SOAP:Envelope>'
    )


@pytest.fixture
def payload_so_return():
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<SOAP:Envelope xmlns:SOAP="http://schemas.xmlsoap.org/soap/envelope/">'
        '<SOAP:Body>'
        '<ns1:notifications xmlns:ns1="http://soap.sforce.com/2005/09/outbound" '
        'xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">'
        '<ns1:Notification>'
        '<ns1:sObject>'
        '<ns2:giic_OrderTypeCode__c>RMA</ns2:giic_OrderTypeCode__c>'
        '<ns2:giic_OrderType__c>Return Order</ns2:giic_OrderType__c>'
        '</ns1:sObject>'
        '</ns1:Notification>'
        '</ns1:notifications>'
        '</SOAP:Body></SOAP:Envelope>'
    )


@pytest.fixture
def payload_unmatched():
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<SOAP:Envelope xmlns:SOAP="http://schemas.xmlsoap.org/soap/envelope/">'
        '<SOAP:Body>'
        '<ns1:notifications xmlns:ns1="http://soap.sforce.com/2005/09/outbound" '
        'xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">'
        '<ns1:Notification>'
        '<ns1:sObject>'
        '<ns2:giic_OrderTypeCode__c>UNKNOWN</ns2:giic_OrderTypeCode__c>'
        '<ns2:giic_OrderType__c>Unknown</ns2:giic_OrderType__c>'
        '</ns1:sObject>'
        '</ns1:Notification>'
        '</ns1:notifications>'
        '</SOAP:Body></SOAP:Envelope>'
    )


@pytest.fixture
def payload_malformed():
    return '<SOAP:Envelope><SOAP:Body><unclosed>' 


def test_happy_path_sales_order(flow_client, payload_sales_order):
    """Assert standard SalesOrder message follows mapping route to EndEvent_2."""
    custom_headers = {
        "Region": "EMEA",
        "Country": "DE",
        "Sender": "Glovia_ERP"
    }
    with unittest.mock.patch.object(requests.Session, "send", side_effect=mock_cpi_pipeline_dispatch):
        response = flow_client.execute(payload_sales_order, headers=custom_headers)

    assert response.status_code == 200
    assert response.headers["SAP-Route-Taken"] == "SequenceFlow_82"
    assert response.headers["SAP-End-Event"] == "EndEvent_2"
    assert response.headers["SAP-Mapped"] == "true"
    assert response.headers["SAP-Custom-Region"] == "EMEA"
    assert response.headers["SAP-Custom-Country"] == "DE"
    assert b"SalesOrder Processed Successfully" in response.content


def test_happy_path_return_sales_order(flow_client, payload_so_return):
    """Assert Return SalesOrder message routes through SO Return branch to EndEvent_9."""
    with unittest.mock.patch.object(requests.Session, "send", side_effect=mock_cpi_pipeline_dispatch):
        response = flow_client.execute(payload_so_return)

    assert response.status_code == 200
    assert response.headers["SAP-Route-Taken"] == "SequenceFlow_113"
    assert response.headers["SAP-End-Event"] == "EndEvent_9"
    assert response.headers["SAP-Mapped"] == "false"
    assert b"ReturnSalesOrder Processed Successfully" in response.content


def test_default_route_escalation(flow_client, payload_unmatched):
    """Assert unhandled order type falls back to default route triggering EscalationEndEvent."""
    with unittest.mock.patch.object(requests.Session, "send", side_effect=mock_cpi_pipeline_dispatch):
        response = flow_client.execute(payload_unmatched)

    assert response.status_code == 409
    assert response.headers["SAP-Route-Taken"] == "SequenceFlow_110"
    assert response.headers["SAP-End-Event"] == "EndEvent_11"
    assert response.headers["SAP-EscalationCode"] == "RoutingConditionNotMet"
    body = json.loads(response.text)
    assert body["error"] == "EscalationTriggered"


def test_exception_subprocess_execution(flow_client, payload_malformed):
    """Assert malformed payload triggers SubProcess_91, restores payload, and ends at EndEvent_95."""
    with unittest.mock.patch.object(requests.Session, "send", side_effect=mock_cpi_pipeline_dispatch):
        response = flow_client.execute(payload_malformed)

    assert response.status_code == 500
    assert response.headers["SAP-Exception-SubProcess"] == "SubProcess_91"
    assert response.headers["SAP-Error-End"] == "EndEvent_95"
    assert response.headers["SAP-Payload-Restored"] == "True"
    body = json.loads(response.text)
    assert body["fault"] == "XMLParsingError"
    assert body["loggedAttachment"] == "Payload"


def test_network_failure_handling(flow_client, payload_sales_order):
    """Verify timeout or network exceptions during adapter call are trapped cleanly without hanging."""
    with unittest.mock.patch.object(requests.Session, "send", side_effect=requests.exceptions.ConnectTimeout("Connection timed out")):
        with pytest.raises(requests.exceptions.ConnectTimeout):
            flow_client.execute(payload_sales_order)
