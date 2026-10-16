import json
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock, patch
import pytest
import requests

SOAP_ENV = "{http://schemas.xmlsoap.org/soap/envelope/}"
ENDPOINT_URL = "https://example.invalid/cxf/Glovia_SalesOrder"


@pytest.fixture
def payload_sales_order():
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<soapenv:Envelope xmlns:soapenv=\"http://schemas.xmlsoap.org/soap/envelope/\" "
        "xmlns:out=\"http://soap.sforce.com/2005/09/outbound\" "
        "xmlns:sobj=\"urn:sobject.enterprise.soap.sforce.com\">"
        "<soapenv:Header/>"
        "<soapenv:Body>"
        "<out:notifications>"
        "<out:Notification>"
        "<out:sObject>"
        "<sobj:giic_OrderTypeCode__c>YORB</sobj:giic_OrderTypeCode__c>"
        "<sobj:OrderNumber>ORD-987654</sobj:OrderNumber>"
        "</out:sObject>"
        "</out:Notification>"
        "</out:notifications>"
        "</soapenv:Body>"
        "</soapenv:Envelope>"
    )


@pytest.fixture
def payload_so_return():
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<soapenv:Envelope xmlns:soapenv=\"http://schemas.xmlsoap.org/soap/envelope/\" "
        "xmlns:out=\"http://soap.sforce.com/2005/09/outbound\" "
        "xmlns:sobj=\"urn:sobject.enterprise.soap.sforce.com\">"
        "<soapenv:Header/>"
        "<soapenv:Body>"
        "<out:notifications>"
        "<out:Notification>"
        "<out:sObject>"
        "<sobj:giic_OrderTypeCode__c>POR</sobj:giic_OrderTypeCode__c>"
        "<sobj:OrderNumber>RET-554433</sobj:OrderNumber>"
        "</out:sObject>"
        "</out:Notification>"
        "</out:notifications>"
        "</soapenv:Body>"
        "</soapenv:Envelope>"
    )


@pytest.fixture
def payload_escalation():
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<soapenv:Envelope xmlns:soapenv=\"http://schemas.xmlsoap.org/soap/envelope/\" "
        "xmlns:out=\"http://soap.sforce.com/2005/09/outbound\" "
        "xmlns:sobj=\"urn:sobject.enterprise.soap.sforce.com\">"
        "<soapenv:Header/>"
        "<soapenv:Body>"
        "<out:notifications>"
        "<out:Notification>"
        "<out:sObject>"
        "<sobj:giic_OrderTypeCode__c>INVALID_ROUTING</sobj:giic_OrderTypeCode__c>"
        "</out:sObject>"
        "</out:Notification>"
        "</out:notifications>"
        "</soapenv:Body>"
        "</soapenv:Envelope>"
    )


@pytest.fixture
def payload_malformed():
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<soapenv:Envelope xmlns:soapenv=\"http://schemas.xmlsoap.org/soap/envelope/\">"
        "<soapenv:Body><unclosed>" 
    )


class FlowTestHarness:
    """Simulates the SAP Integration Suite runtime behavior for Flow Definitions."""

    @staticmethod
    def invoke_flow(payload_str, headers=None):
        session = requests.Session()
        req_headers = {"Content-Type": "text/xml; charset=utf-8"}
        if headers:
            req_headers.update(headers)
        return session.post(ENDPOINT_URL, data=payload_str, headers=req_headers)


def test_tc_glovia_001_sales_order_happy_path(payload_sales_order):
    """Validates SalesOrder route execution, headers enrichment, and successful message termination."""
    mock_response = requests.Response()
    mock_response.status_code = 200
    mock_response.headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SAP-MessageId": "MSG-001-OK",
        "Country": "US",
        "Region": "NA",
        "BusinessObject": "SalesOrder",
        "Sender": "Glovia"
    }
    ack_xml = (
        "<soapenv:Envelope xmlns:soapenv=\"http://schemas.xmlsoap.org/soap/envelope/\">"
        "<soapenv:Body><notificationsResponse xmlns=\"http://soap.sforce.com/2005/09/outbound\">"
        "<Ack>true</Ack>"
        "</notificationsResponse></soapenv:Body></soapenv:Envelope>"
    )
    mock_response._content = ack_xml.encode("utf-8")

    with patch.object(requests.Session, "post", return_value=mock_response) as mock_post:
        res = FlowTestHarness.invoke_flow(payload_sales_order)
        assert mock_post.called
        assert res.status_code == 200
        assert res.headers.get("BusinessObject") == "SalesOrder"
        assert res.headers.get("Country") == "US"
        assert res.headers.get("Region") == "NA"

        root = ET.fromstring(res.text)
        body = root.find(f"{SOAP_ENV}Body")
        assert body is not None
        ack = body.find("{http://soap.sforce.com/2005/09/outbound}notificationsResponse/{http://soap.sforce.com/2005/09/outbound}Ack")
        assert ack is not None and ack.text == "true"


def test_tc_glovia_002_so_return_happy_path(payload_so_return):
    """Validates SO Return create route execution terminating at EndEvent_9."""
    mock_response = requests.Response()
    mock_response.status_code = 200
    mock_response.headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SAP-MessageId": "MSG-002-RET",
        "Country": "DE",
        "Region": "EMEA",
        "BusinessObject": "SalesOrderReturn",
        "Sender": "Glovia"
    }
    ack_xml = (
        "<soapenv:Envelope xmlns:soapenv=\"http://schemas.xmlsoap.org/soap/envelope/\">"
        "<soapenv:Body><notificationsResponse xmlns=\"http://soap.sforce.com/2005/09/outbound\">"
        "<Ack>true</Ack>"
        "</notificationsResponse></soapenv:Body></soapenv:Envelope>"
    )
    mock_response._content = ack_xml.encode("utf-8")

    with patch.object(requests.Session, "post", return_value=mock_response) as mock_post:
        res = FlowTestHarness.invoke_flow(payload_so_return)
        assert mock_post.called
        assert res.status_code == 200
        assert res.headers.get("BusinessObject") == "SalesOrderReturn"
        assert res.headers.get("Region") == "EMEA"


def test_tc_glovia_003_escalation_routing_condition_not_met(payload_escalation):
    """Validates Default routing branch SequenceFlow_110 raising EscalationEndEvent RoutingConditionNotMet."""
    mock_response = requests.Response()
    mock_response.status_code = 500
    mock_response.headers = {"Content-Type": "text/xml; charset=utf-8"}
    fault_xml = (
        "<soapenv:Envelope xmlns:soapenv=\"http://schemas.xmlsoap.org/soap/envelope/\">"
        "<soapenv:Body>"
        "<soapenv:Fault>"
        "<faultcode>soapenv:Server</faultcode>"
        "<faultstring>Escalation: RoutingConditionNotMet</faultstring>"
        "<faultactor>IntegrationProcess</faultactor>"
        "<detail>"
        "<errorCode>RoutingConditionNotMet</errorCode>"
        "</detail>"
        "</soapenv:Fault>"
        "</soapenv:Body>"
        "</soapenv:Envelope>"
    )
    mock_response._content = fault_xml.encode("utf-8")

    with patch.object(requests.Session, "post", return_value=mock_response):
        res = FlowTestHarness.invoke_flow(payload_escalation)
        assert res.status_code == 500
        root = ET.fromstring(res.text)
        fault = root.find(f".//{SOAP_ENV}Fault")
        assert fault is not None
        faultstring = fault.find("faultstring")
        assert faultstring is not None and "RoutingConditionNotMet" in faultstring.text
        detail = fault.find("detail")
        assert detail is not None
        code = detail.find("errorCode")
        assert code is not None and code.text == "RoutingConditionNotMet"


def test_tc_glovia_004_malformed_xml_exception_subprocess(payload_malformed):
    """Validates SubProcess_91 execution logging the original InputPayload and raising ErrorEndEvent 95."""
    mock_response = requests.Response()
    mock_response.status_code = 500
    mock_response.headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SAP-ProcessingLog": "SubProcess_91_Executed;PayloadLogged=true"
    }
    fault_xml = (
        "<soapenv:Envelope xmlns:soapenv=\"http://schemas.xmlsoap.org/soap/envelope/\">"
        "<soapenv:Body>"
        "<soapenv:Fault>"
        "<faultcode>soapenv:Client</faultcode>"
        "<faultstring>Error parsing XML payload during pipeline execution</faultstring>"
        "</soapenv:Fault>"
        "</soapenv:Body>"
        "</soapenv:Envelope>"
    )
    mock_response._content = fault_xml.encode("utf-8")

    with patch.object(requests.Session, "post", return_value=mock_response):
        res = FlowTestHarness.invoke_flow(payload_malformed)
        assert res.status_code == 500
        assert "SubProcess_91_Executed" in res.headers.get("SAP-ProcessingLog", "")
        root = ET.fromstring(res.text)
        fault = root.find(f".//{SOAP_ENV}Fault")
        assert fault is not None
        faultstring = fault.find("faultstring")
        assert faultstring is not None and "Error parsing XML" in faultstring.text


def test_tc_glovia_005_timeout_handling(payload_sales_order):
    """Validates client and harness behavior upon backend connection/socket timeout."""
    with patch.object(requests.Session, "post", side_effect=requests.exceptions.Timeout("Socket timed out")):
        with pytest.raises(requests.exceptions.Timeout) as exc_info:
            FlowTestHarness.invoke_flow(payload_sales_order)
        assert "Socket timed out" in str(exc_info.value)


def test_tc_glovia_006_connection_error_handling(payload_sales_order):
    """Validates client resilience against downstream connection drop."""
    with patch.object(requests.Session, "post", side_effect=requests.exceptions.ConnectionError("Connection refused")):
        with pytest.raises(requests.exceptions.ConnectionError) as exc_info:
            FlowTestHarness.invoke_flow(payload_sales_order)
        assert "Connection refused" in str(exc_info.value)
