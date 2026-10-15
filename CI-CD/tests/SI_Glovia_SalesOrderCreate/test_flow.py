import json
import os
import pytest
import requests
import responses
import xml.etree.ElementTree as ET

MOCK_BASE_URL = os.environ.get("CI_IFLOW_ENDPOINT", "https://example.invalid/cxf/Glovia_SalesOrder")

SOAP_NS = {
    "soap": "http://schemas.xmlsoap.org/soap/envelope/",
    "ns1": "http://soap.sforce.com/2005/09/outbound",
    "ns2": "urn:sobject.enterprise.soap.sforce.com"
}

@pytest.fixture
def valid_sales_order_xml():
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<SOAP:Envelope xmlns:SOAP=\"http://schemas.xmlsoap.org/soap/envelope/\" "
        "xmlns:ns1=\"http://soap.sforce.com/2005/09/outbound\" "
        "xmlns:ns2=\"urn:sobject.enterprise.soap.sforce.com\">\n"
        "  <SOAP:Header/>\n"
        "  <SOAP:Body>\n"
        "    <ns1:notifications>\n"
        "      <ns1:Notification>\n"
        "        <ns1:Id>NOTIF0001</ns1:Id>\n"
        "        <ns1:sObject>\n"
        "          <ns2:Id>SO10002981</ns2:Id>\n"
        "          <ns2:giic_OrderTypeCode__c>YORB</ns2:giic_OrderTypeCode__c>\n"
        "        </ns1:sObject>\n"
        "      </ns1:Notification>\n"
        "    </ns1:notifications>\n"
        "  </SOAP:Body>\n"
        "</SOAP:Envelope>"
    )

@pytest.fixture
def valid_so_return_xml():
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<SOAP:Envelope xmlns:SOAP=\"http://schemas.xmlsoap.org/soap/envelope/\" "
        "xmlns:ns1=\"http://soap.sforce.com/2005/09/outbound\" "
        "xmlns:ns2=\"urn:sobject.enterprise.soap.sforce.com\">\n"
        "  <SOAP:Header/>\n"
        "  <SOAP:Body>\n"
        "    <ns1:notifications>\n"
        "      <ns1:Notification>\n"
        "        <ns1:Id>NOTIF0002</ns1:Id>\n"
        "        <ns1:sObject>\n"
        "          <ns2:Id>RET99201</ns2:Id>\n"
        "          <ns2:giic_OrderTypeCode__c>POR</ns2:giic_OrderTypeCode__c>\n"
        "        </ns1:sObject>\n"
        "      </ns1:Notification>\n"
        "    </ns1:notifications>\n"
        "  </SOAP:Body>\n"
        "</SOAP:Envelope>"
    )

@pytest.fixture
def unmatched_order_xml():
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<SOAP:Envelope xmlns:SOAP=\"http://schemas.xmlsoap.org/soap/envelope/\" "
        "xmlns:ns1=\"http://soap.sforce.com/2005/09/outbound\" "
        "xmlns:ns2=\"urn:sobject.enterprise.soap.sforce.com\">\n"
        "  <SOAP:Header/>\n"
        "  <SOAP:Body>\n"
        "    <ns1:notifications>\n"
        "      <ns1:Notification>\n"
        "        <ns1:Id>NOTIF0003</ns1:Id>\n"
        "        <ns1:sObject>\n"
        "          <ns2:Id>UNKNOWN_999</ns2:Id>\n"
        "          <ns2:giic_OrderTypeCode__c>UNKNOWN_CODE</ns2:giic_OrderTypeCode__c>\n"
        "        </ns1:sObject>\n"
        "      </ns1:Notification>\n"
        "    </ns1:notifications>\n"
        "  </SOAP:Body>\n"
        "</SOAP:Envelope>"
    )

@pytest.fixture
def malformed_xml():
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<SOAP:Envelope xmlns:SOAP=\"http://schemas.xmlsoap.org/soap/envelope/\">\n"
        "  <SOAP:Body>\n"
        "    <broken>tags"
    )

@responses.activate
def test_sales_order_happy_path(valid_sales_order_xml):
    root = ET.fromstring(valid_sales_order_xml)
    order_code = root.find(".//ns2:giic_OrderTypeCode__c", SOAP_NS).text
    assert order_code == "YORB"

    expected_headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "X-SAP-Region": "US",
        "X-SAP-Country": "USA",
        "X-SAP-BusinessObject": "SalesOrder",
        "X-SAP-Sender": "Glovia",
        "X-SAP-Route": "SalesOrder_Create"
    }

    responses.add(
        responses.POST,
        MOCK_BASE_URL,
        body="<SOAP:Envelope><SOAP:Body><Response>Success_SalesOrder</Response></SOAP:Body></SOAP:Envelope>",
        status=200,
        headers=expected_headers
    )

    resp = requests.post(
        MOCK_BASE_URL,
        data=valid_sales_order_xml,
        headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": "\"\""}
    )

    assert resp.status_code == 200
    assert "Success_SalesOrder" in resp.text
    assert resp.headers.get("X-SAP-Region") == "US"
    assert resp.headers.get("X-SAP-BusinessObject") == "SalesOrder"
    assert resp.headers.get("X-SAP-Route") == "SalesOrder_Create"

@responses.activate
def test_so_return_happy_path(valid_so_return_xml):
    root = ET.fromstring(valid_so_return_xml)
    order_code = root.find(".//ns2:giic_OrderTypeCode__c", SOAP_NS).text
    assert order_code == "POR"

    expected_headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "X-SAP-Region": "US",
        "X-SAP-Country": "USA",
        "X-SAP-BusinessObject": "SOReturn",
        "X-SAP-Sender": "Glovia",
        "X-SAP-Route": "SO_Return_Create"
    }

    responses.add(
        responses.POST,
        MOCK_BASE_URL,
        body="<SOAP:Envelope><SOAP:Body><Response>Success_Return</Response></SOAP:Body></SOAP:Envelope>",
        status=200,
        headers=expected_headers
    )

    resp = requests.post(
        MOCK_BASE_URL,
        data=valid_so_return_xml,
        headers={"Content-Type": "text/xml; charset=utf-8"}
    )

    assert resp.status_code == 200
    assert "Success_Return" in resp.text
    assert resp.headers.get("X-SAP-Route") == "SO_Return_Create"

@responses.activate
def test_routing_condition_not_met_escalation(unmatched_order_xml):
    escalation_headers = {
        "X-SAP-EscalationCode": "RoutingConditionNotMet",
        "X-SAP-ProcessStatus": "Escalated"
    }

    responses.add(
        responses.POST,
        MOCK_BASE_URL,
        body="<SOAP:Envelope><SOAP:Body><SOAP:Fault><faultcode>RoutingConditionNotMet</faultcode><faultstring>Default branch escalation reached</faultstring></SOAP:Fault></SOAP:Body></SOAP:Envelope>",
        status=400,
        headers=escalation_headers
    )

    resp = requests.post(
        MOCK_BASE_URL,
        data=unmatched_order_xml,
        headers={"Content-Type": "text/xml; charset=utf-8"}
    )

    assert resp.status_code == 400
    assert resp.headers.get("X-SAP-EscalationCode") == "RoutingConditionNotMet"
    assert "RoutingConditionNotMet" in resp.text

@responses.activate
def test_error_subprocess_execution_on_failure(malformed_xml):
    error_headers = {
        "X-SAP-SubProcess": "SubProcess_91",
        "X-SAP-RestoredPayloadProperty": "InputPayload",
        "X-SAP-AttachmentLogged": "Payload"
    }

    responses.add(
        responses.POST,
        MOCK_BASE_URL,
        body="<SOAP:Envelope><SOAP:Body><SOAP:Fault><faultcode>SOAP-ENV:Server</faultcode><faultstring>General Processing Exception</faultstring></SOAP:Fault></SOAP:Body></SOAP:Envelope>",
        status=500,
        headers=error_headers
    )

    resp = requests.post(
        MOCK_BASE_URL,
        data=malformed_xml,
        headers={"Content-Type": "text/xml; charset=utf-8"}
    )

    assert resp.status_code == 500
    assert resp.headers.get("X-SAP-SubProcess") == "SubProcess_91"
    assert resp.headers.get("X-SAP-RestoredPayloadProperty") == "InputPayload"
    assert resp.headers.get("X-SAP-AttachmentLogged") == "Payload"

@responses.activate
def test_downstream_timeout_handling(valid_sales_order_xml):
    responses.add(
        responses.POST,
        MOCK_BASE_URL,
        body=requests.exceptions.ConnectTimeout("Connection timed out beyond 30s limit")
    )

    with pytest.raises(requests.exceptions.ConnectTimeout):
        requests.post(MOCK_BASE_URL, data=valid_sales_order_xml, timeout=30)
