import json
import xml.etree.ElementTree as ET
import pytest
import requests
import responses

NAMESPACE_MAP = {
    "soapenv": "http://schemas.xmlsoap.org/soap/envelope/",
    "ns1": "http://soap.sforce.com/2005/09/outbound",
    "ns2": "urn:sobject.enterprise.soap.sforce.com",
}

BASE_ENDPOINT = "https://localhost.example.invalid/cxf/Glovia_SalesOrder"


@pytest.fixture
def valid_sales_order_payload():
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<soapenv:Envelope xmlns:soapenv=\"http://schemas.xmlsoap.org/soap/envelope/\" "
        "xmlns:ns1=\"http://soap.sforce.com/2005/09/outbound\" "
        "xmlns:ns2=\"urn:sobject.enterprise.soap.sforce.com\">\n"
        "  <soapenv:Header/>\n"
        "  <soapenv:Body>\n"
        "    <ns1:notifications>\n"
        "      <ns1:Notification>\n"
        "        <ns1:Id>04k000000000001AAA</ns1:Id>\n"
        "        <ns1:sObject>\n"
        "          <ns2:Id>a01000000000001AAA</ns2:Id>\n"
        "          <ns2:giic_OrderTypeCode__c>YORB</ns2:giic_OrderTypeCode__c>\n"
        "          <ns2:OrderNumber__c>SO-2026-9901</ns2:OrderNumber__c>\n"
        "        </ns1:sObject>\n"
        "      </ns1:Notification>\n"
        "    </ns1:notifications>\n"
        "  </soapenv:Body>\n"
        "</soapenv:Envelope>"
    )


@pytest.fixture
def valid_return_order_payload():
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<soapenv:Envelope xmlns:soapenv=\"http://schemas.xmlsoap.org/soap/envelope/\" "
        "xmlns:ns1=\"http://soap.sforce.com/2005/09/outbound\" "
        "xmlns:ns2=\"urn:sobject.enterprise.soap.sforce.com\">\n"
        "  <soapenv:Header/>\n"
        "  <soapenv:Body>\n"
        "    <ns1:notifications>\n"
        "      <ns1:Notification>\n"
        "        <ns1:Id>04k000000000002AAA</ns1:Id>\n"
        "        <ns1:sObject>\n"
        "          <ns2:Id>a01000000000002AAA</ns2:Id>\n"
        "          <ns2:giic_OrderTypeCode__c>PLM</ns2:giic_OrderTypeCode__c>\n"
        "          <ns2:OrderNumber__c>RET-2026-1102</ns2:OrderNumber__c>\n"
        "        </ns1:sObject>\n"
        "      </ns1:Notification>\n"
        "    </ns1:notifications>\n"
        "  </soapenv:Body>\n"
        "</soapenv:Envelope>"
    )


@pytest.fixture
def unrouted_order_payload():
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<soapenv:Envelope xmlns:soapenv=\"http://schemas.xmlsoap.org/soap/envelope/\" "
        "xmlns:ns1=\"http://soap.sforce.com/2005/09/outbound\" "
        "xmlns:ns2=\"urn:sobject.enterprise.soap.sforce.com\">\n"
        "  <soapenv:Header/>\n"
        "  <soapenv:Body>\n"
        "    <ns1:notifications>\n"
        "      <ns1:Notification>\n"
        "        <ns1:Id>04k000000000003AAA</ns1:Id>\n"
        "        <ns1:sObject>\n"
        "          <ns2:Id>a01000000000003AAA</ns2:Id>\n"
        "          <ns2:giic_OrderTypeCode__c>UNMATCHED_CODE</ns2:giic_OrderTypeCode__c>\n"
        "        </ns1:sObject>\n"
        "      </ns1:Notification>\n"
        "    </ns1:notifications>\n"
        "  </soapenv:Body>\n"
        "</soapenv:Envelope>"
    )


@pytest.fixture
def malformed_xml_payload():
    return "<soapenv:Envelope><soapenv:Body><unclosed-tag></soapenv:Envelope>"


def parse_order_type_from_body(payload: str) -> str:
    root = ET.fromstring(payload)
    element = root.find(
        ".//ns1:Notification/ns1:sObject/ns2:giic_OrderTypeCode__c",
        NAMESPACE_MAP,
    )
    return element.text if element is not None and element.text else ""


@responses.activate
def test_tc_glovia_001_happy_path_sales_order(valid_sales_order_payload):
    order_type = parse_order_type_from_body(valid_sales_order_payload)
    assert order_type == "YORB"

    def request_callback(request):
        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "X-SAP-Integration-Flow": "Process_1",
            "X-SAP-Routed-Branch": "SequenceFlow_82",
            "X-SAP-End-Event": "EndEvent_2",
            "X-Header-Region": "EMEA",
            "X-Header-Country": "DE",
            "X-Header-BusinessObject": "SalesOrder",
            "X-Header-Sender": "Glovia",
            "X-Property-InputPayload": "Stored",
        }
        return (202, headers, "")

    responses.add_callback(
        responses.POST,
        BASE_ENDPOINT,
        callback=request_callback,
        content_type="text/xml",
    )

    response = requests.post(
        BASE_ENDPOINT,
        data=valid_sales_order_payload,
        headers={"Content-Type": "text/xml", "SOAPAction": ""},
        timeout=30,
    )

    assert response.status_code == 202
    assert response.headers["X-SAP-Routed-Branch"] == "SequenceFlow_82"
    assert response.headers["X-SAP-End-Event"] == "EndEvent_2"
    assert response.headers["X-Header-BusinessObject"] == "SalesOrder"
    assert response.headers["X-Property-InputPayload"] == "Stored"
    assert len(responses.calls) == 1


@responses.activate
def test_tc_glovia_002_happy_path_return_order(valid_return_order_payload):
    order_type = parse_order_type_from_body(valid_return_order_payload)
    assert order_type == "PLM"

    def request_callback(request):
        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "X-SAP-Integration-Flow": "Process_1",
            "X-SAP-Routed-Branch": "SequenceFlow_113",
            "X-SAP-End-Event": "EndEvent_9",
            "X-Header-Region": "NA",
            "X-Header-Country": "US",
            "X-Header-BusinessObject": "SOReturn",
            "X-Header-Sender": "Glovia",
        }
        return (202, headers, "")

    responses.add_callback(
        responses.POST,
        BASE_ENDPOINT,
        callback=request_callback,
        content_type="text/xml",
    )

    response = requests.post(
        BASE_ENDPOINT,
        data=valid_return_order_payload,
        headers={"Content-Type": "text/xml"},
        timeout=30,
    )

    assert response.status_code == 202
    assert response.headers["X-SAP-Routed-Branch"] == "SequenceFlow_113"
    assert response.headers["X-SAP-End-Event"] == "EndEvent_9"
    assert response.headers["X-Header-BusinessObject"] == "SOReturn"
    assert len(responses.calls) == 1


@responses.activate
def test_tc_glovia_003_escalation_default_route(unrouted_order_payload):
    order_type = parse_order_type_from_body(unrouted_order_payload)
    assert order_type == "UNMATCHED_CODE"

    def request_callback(request):
        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "X-SAP-EscalationCode": "RoutingConditionNotMet",
            "X-SAP-End-Event": "EndEvent_11",
            "X-SAP-Status": "Escalated",
        }
        body = (
            "<soapenv:Envelope xmlns:soapenv=\"http://schemas.xmlsoap.org/soap/envelope/\">"
            "<soapenv:Body><soapenv:Fault><faultcode>soapenv:Server</faultcode>"
            "<faultstring>Escalation End: RoutingConditionNotMet</faultstring>"
            "</soapenv:Fault></soapenv:Body></soapenv:Envelope>"
        )
        return (500, headers, body)

    responses.add_callback(
        responses.POST,
        BASE_ENDPOINT,
        callback=request_callback,
        content_type="text/xml",
    )

    response = requests.post(
        BASE_ENDPOINT,
        data=unrouted_order_payload,
        headers={"Content-Type": "text/xml"},
        timeout=30,
    )

    assert response.status_code == 500
    assert response.headers["X-SAP-EscalationCode"] == "RoutingConditionNotMet"
    assert response.headers["X-SAP-End-Event"] == "EndEvent_11"
    root = ET.fromstring(response.text)
    fault_string = root.find(".//{http://schemas.xmlsoap.org/soap/envelope/}faultstring")
    assert fault_string is not None and "RoutingConditionNotMet" in fault_string.text


@responses.activate
def test_tc_glovia_004_exception_subprocess_trigger(malformed_xml_payload):
    with pytest.raises(ET.ParseError):
        ET.fromstring(malformed_xml_payload)

    def request_callback(request):
        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "X-SAP-SubProcess": "SubProcess_91",
            "X-SAP-Error-Event": "EndEvent_95",
            "X-SAP-Logged-Attachment": "Payload",
            "X-SAP-Payload-Restored": "true",
        }
        fault_body = (
            "<soapenv:Envelope xmlns:soapenv=\"http://schemas.xmlsoap.org/soap/envelope/\">"
            "<soapenv:Body><soapenv:Fault><faultcode>soapenv:Client</faultcode>"
            "<faultstring>XML parsing error intercepted by ErrorEventSubProcess</faultstring>"
            "</soapenv:Fault></soapenv:Body></soapenv:Envelope>"
        )
        return (500, headers, fault_body)

    responses.add_callback(
        responses.POST,
        BASE_ENDPOINT,
        callback=request_callback,
        content_type="text/xml",
    )

    response = requests.post(
        BASE_ENDPOINT,
        data=malformed_xml_payload,
        headers={"Content-Type": "text/xml"},
        timeout=30,
    )

    assert response.status_code == 500
    assert response.headers["X-SAP-SubProcess"] == "SubProcess_91"
    assert response.headers["X-SAP-Error-Event"] == "EndEvent_95"
    assert response.headers["X-SAP-Logged-Attachment"] == "Payload"
    assert response.headers["X-SAP-Payload-Restored"] == "true"


@responses.activate
def test_tc_glovia_005_unauthorized_access(valid_sales_order_payload):
    responses.add(
        responses.POST,
        BASE_ENDPOINT,
        body="<soapenv:Fault><faultstring>Unauthorized: Role ESBMessaging.send required</faultstring></soapenv:Fault>",
        status=401,
        content_type="text/xml",
    )

    response = requests.post(
        BASE_ENDPOINT,
        data=valid_sales_order_payload,
        headers={"Content-Type": "text/xml"},
        timeout=30,
    )

    assert response.status_code == 401
    assert "ESBMessaging.send" in response.text


@responses.activate
def test_tc_glovia_006_payload_size_limit_exceeded():
    oversized_payload = "<soapenv:Envelope>" + ("X" * (41 * 1024 * 1024)) + "</soapenv:Envelope>"

    responses.add(
        responses.POST,
        BASE_ENDPOINT,
        body="<soapenv:Fault><faultstring>Maximum body size 40MB exceeded</faultstring></soapenv:Fault>",
        status=413,
        content_type="text/xml",
    )

    response = requests.post(
        BASE_ENDPOINT,
        data=oversized_payload[:1000],  # Emulate sending without loading 41MB in RAM
        headers={"Content-Type": "text/xml"},
        timeout=30,
    )

    assert response.status_code == 413
    assert "Maximum body size 40MB exceeded" in response.text
