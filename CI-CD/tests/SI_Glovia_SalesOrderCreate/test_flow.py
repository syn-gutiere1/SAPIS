import os
import json
import xml.etree.ElementTree as ET
import pytest
import requests
import responses

TEST_ENDPOINT = "https://cpi.example.invalid/cxf/Glovia_SalesOrder"

SOAP_HEADERS = {
    "Content-Type": "text/xml; charset=UTF-8",
    "SOAPAction": "\"\""
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
        "        <ns1:Id>04k000000000001</ns1:Id>\n"
        "        <ns1:sObject>\n"
        "          <ns2:Id>a01000000000001</ns2:Id>\n"
        "          <ns2:giic_OrderTypeCode__c>YORB</ns2:giic_OrderTypeCode__c>\n"
        "          <ns2:CustomerNumber>CUST-10023</ns2:CustomerNumber>\n"
        "        </ns1:sObject>\n"
        "      </ns1:Notification>\n"
        "    </ns1:notifications>\n"
        "  </SOAP:Body>\n"
        "</SOAP:Envelope>"
    )

@pytest.fixture
def return_sales_order_xml():
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<SOAP:Envelope xmlns:SOAP=\"http://schemas.xmlsoap.org/soap/envelope/\" "
        "xmlns:ns1=\"http://soap.sforce.com/2005/09/outbound\" "
        "xmlns:ns2=\"urn:sobject.enterprise.soap.sforce.com\">\n"
        "  <SOAP:Header/>\n"
        "  <SOAP:Body>\n"
        "    <ns1:notifications>\n"
        "      <ns1:Notification>\n"
        "        <ns1:Id>04k000000000002</ns1:Id>\n"
        "        <ns1:sObject>\n"
        "          <ns2:Id>a01000000000002</ns2:Id>\n"
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
        "        <ns1:Id>04k000000000003</ns1:Id>\n"
        "        <ns1:sObject>\n"
        "          <ns2:Id>a01000000000003</ns2:Id>\n"
        "          <ns2:giic_OrderTypeCode__c>NON_MATCHING</ns2:giic_OrderTypeCode__c>\n"
        "        </ns1:sObject>\n"
        "      </ns1:Notification>\n"
        "    </ns1:notifications>\n"
        "  </SOAP:Body>\n"
        "</SOAP:Envelope>"
    )

@responses.activate
def test_tc_glovia_001_sales_order_happy_path(valid_sales_order_xml):
    # OneWay SOAP endpoint responds 202 Accepted upon successful handoff to mapping & end event
    responses.add(
        responses.POST,
        TEST_ENDPOINT,
        body="<SOAP:Envelope xmlns:SOAP=\"http://schemas.xmlsoap.org/soap/envelope/\"><SOAP:Body/></SOAP:Envelope>",
        status=202,
        headers={
            "Content-Type": "text/xml",
            "SAP-Processing-Route": "SequenceFlow_82",
            "SAP-CustomHeader-BusinessObject": "SalesOrder",
            "SAP-CustomHeader-Country": "US",
            "SAP-CustomHeader-Region": "AMER",
            "SAP-CustomHeader-Sender": "Glovia"
        }
    )

    response = requests.post(TEST_ENDPOINT, data=valid_sales_order_xml, headers=SOAP_HEADERS, timeout=10)
    assert response.status_code == 202
    assert response.headers.get("SAP-Processing-Route") == "SequenceFlow_82"
    assert response.headers.get("SAP-CustomHeader-BusinessObject") == "SalesOrder"
    assert response.headers.get("SAP-CustomHeader-Country") == "US"

@responses.activate
def test_tc_glovia_002_sales_order_return_route(return_sales_order_xml):
    responses.add(
        responses.POST,
        TEST_ENDPOINT,
        body="<SOAP:Envelope xmlns:SOAP=\"http://schemas.xmlsoap.org/soap/envelope/\"><SOAP:Body/></SOAP:Envelope>",
        status=202,
        headers={
            "Content-Type": "text/xml",
            "SAP-Processing-Route": "SequenceFlow_113",
            "SAP-CustomHeader-BusinessObject": "SalesOrderReturn",
            "SAP-CustomHeader-Sender": "Glovia"
        }
    )

    response = requests.post(TEST_ENDPOINT, data=return_sales_order_xml, headers=SOAP_HEADERS, timeout=10)
    assert response.status_code == 202
    assert response.headers.get("SAP-Processing-Route") == "SequenceFlow_113"
    assert response.headers.get("SAP-CustomHeader-BusinessObject") == "SalesOrderReturn"

@responses.activate
def test_tc_glovia_003_routing_default_escalation(unmatched_order_xml):
    responses.add(
        responses.POST,
        TEST_ENDPOINT,
        body="<SOAP:Envelope xmlns:SOAP=\"http://schemas.xmlsoap.org/soap/envelope/\"><SOAP:Body><SOAP:Fault><faultcode>SOAP:Client</faultcode><faultstring>Escalation: RoutingConditionNotMet</faultstring></SOAP:Fault></SOAP:Body></SOAP:Envelope>",
        status=200,
        headers={
            "Content-Type": "text/xml",
            "SAP-Escalation-Code": "RoutingConditionNotMet",
            "SAP-Processing-Route": "SequenceFlow_110"
        }
    )

    response = requests.post(TEST_ENDPOINT, data=unmatched_order_xml, headers=SOAP_HEADERS, timeout=10)
    assert response.status_code == 200
    assert response.headers.get("SAP-Escalation-Code") == "RoutingConditionNotMet"
    root = ET.fromstring(response.text)
    fault_string = root.find(".//{http://schemas.xmlsoap.org/soap/envelope/}faultstring")
    assert fault_string is not None
    assert "RoutingConditionNotMet" in fault_string.text

@responses.activate
def test_tc_glovia_004_mapping_error_subprocess():
    corrupted_payload = "<SOAP:Envelope><SOAP:Body><Corrupt/></SOAP:Body></SOAP:Envelope>"
    responses.add(
        responses.POST,
        TEST_ENDPOINT,
        body="<SOAP:Envelope xmlns:SOAP=\"http://schemas.xmlsoap.org/soap/envelope/\"><SOAP:Body><SOAP:Fault><faultcode>SOAP:Server</faultcode><faultstring>Processing Error</faultstring></SOAP:Fault></SOAP:Body></SOAP:Envelope>",
        status=500,
        headers={
            "Content-Type": "text/xml",
            "SAP-ErrorSubprocess-Executed": "SubProcess_91",
            "SAP-Payload-Restored": "true",
            "SAP-Log-Attachment": "Payload"
        }
    )

    response = requests.post(TEST_ENDPOINT, data=corrupted_payload, headers=SOAP_HEADERS, timeout=10)
    assert response.status_code == 500
    assert response.headers.get("SAP-ErrorSubprocess-Executed") == "SubProcess_91"
    assert response.headers.get("SAP-Payload-Restored") == "true"
    assert response.headers.get("SAP-Log-Attachment") == "Payload"

@responses.activate
def test_tc_glovia_005_network_timeout_handling(valid_sales_order_xml):
    responses.add(
        responses.POST,
        TEST_ENDPOINT,
        body=requests.exceptions.Timeout("Simulated gateway timeout")
    )

    with pytest.raises(requests.exceptions.Timeout):
        requests.post(TEST_ENDPOINT, data=valid_sales_order_xml, headers=SOAP_HEADERS, timeout=1)
