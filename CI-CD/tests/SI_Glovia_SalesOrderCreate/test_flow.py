import json
import xml.etree.ElementTree as ET
import pytest
import responses
import requests

SOAP_ENV = "{http://schemas.xmlsoap.org/soap/envelope/}"
ENDPOINT_URL = "https://cpi-dev.example.invalid/cxf/Glovia_SalesOrder"

@pytest.fixture
def valid_sales_order_xml():
    return """<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ns1="http://soap.sforce.com/2005/09/outbound" xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">
  <soapenv:Header/>
  <soapenv:Body>
    <ns1:notifications>
      <ns1:Notification>
        <ns1:Id>04k000000000001AAA</ns1:Id>
        <ns1:sObject>
          <ns2:Id>a01000000000001AAA</ns2:Id>
          <ns2:giic_OrderTypeCode__c>YORB</ns2:giic_OrderTypeCode__c>
          <ns2:CustomerNumber__c>CUST-10023</ns2:CustomerNumber__c>
          <ns2:TotalAmount__c>15420.50</ns2:TotalAmount__c>
        </ns1:sObject>
      </ns1:Notification>
    </ns1:notifications>
  </soapenv:Body>
</soapenv:Envelope>"""

@pytest.fixture
def valid_return_order_xml():
    return """<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ns1="http://soap.sforce.com/2005/09/outbound" xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">
  <soapenv:Header/>
  <soapenv:Body>
    <ns1:notifications>
      <ns1:Notification>
        <ns1:Id>04k000000000002BBB</ns1:Id>
        <ns1:sObject>
          <ns2:Id>a01000000000002BBB</ns2:Id>
          <ns2:giic_OrderTypeCode__c>POR</ns2:giic_OrderTypeCode__c>
        </ns1:sObject>
      </ns1:Notification>
    </ns1:notifications>
  </soapenv:Body>
</soapenv:Envelope>"""

@pytest.fixture
def unmatched_order_xml():
    return """<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ns1="http://soap.sforce.com/2005/09/outbound" xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">
  <soapenv:Header/>
  <soapenv:Body>
    <ns1:notifications>
      <ns1:Notification>
        <ns1:Id>04k000000000003CCC</ns1:Id>
        <ns1:sObject>
          <ns2:Id>a01000000000003CCC</ns2:Id>
          <ns2:giic_OrderTypeCode__c>INVALID_ROUTING</ns2:giic_OrderTypeCode__c>
        </ns1:sObject>
      </ns1:Notification>
    </ns1:notifications>
  </soapenv:Body>
</soapenv:Envelope>"""

@responses.activate
def test_sales_order_happy_path(valid_sales_order_xml):
    success_response = """<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
  <soapenv:Body>
    <notificationsResponse xmlns="http://soap.sforce.com/2005/09/outbound">
      <Ack>true</Ack>
    </notificationsResponse>
  </soapenv:Body>
</soapenv:Envelope>"""
    responses.add(
        responses.POST,
        ENDPOINT_URL,
        body=success_response,
        status=200,
        content_type="text/xml; charset=utf-8",
        headers={
            "SAP_MessageProcessingLogID": "AF_TEST_LOG_001",
            "X-Route-Executed": "SequenceFlow_82",
            "Region": "EMEA",
            "Country": "DE",
            "BusinessObject": "SalesOrder"
        }
    )

    headers = {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": "\"\""}
    response = requests.post(ENDPOINT_URL, data=valid_sales_order_xml, headers=headers, timeout=10)

    assert response.status_code == 200
    assert response.headers.get("X-Route-Executed") == "SequenceFlow_82"
    assert response.headers.get("BusinessObject") == "SalesOrder"
    assert response.headers.get("Region") == "EMEA"

    root = ET.fromstring(response.text)
    ack_elem = root.find(".//{http://soap.sforce.com/2005/09/outbound}Ack")
    assert ack_elem is not None
    assert ack_elem.text == "true"

@responses.activate
def test_so_return_happy_path(valid_return_order_xml):
    responses.add(
        responses.POST,
        ENDPOINT_URL,
        body="<soapenv:Envelope xmlns:soapenv='http://schemas.xmlsoap.org/soap/envelope/'><soapenv:Body><Ack>true</Ack></soapenv:Body></soapenv:Envelope>",
        status=200,
        content_type="text/xml; charset=utf-8",
        headers={
            "SAP_MessageProcessingLogID": "AF_TEST_LOG_002",
            "X-Route-Executed": "SequenceFlow_113",
            "BusinessObject": "SalesOrderReturn"
        }
    )

    headers = {"Content-Type": "text/xml; charset=utf-8"}
    response = requests.post(ENDPOINT_URL, data=valid_return_order_xml, headers=headers, timeout=10)

    assert response.status_code == 200
    assert response.headers.get("X-Route-Executed") == "SequenceFlow_113"
    assert response.headers.get("BusinessObject") == "SalesOrderReturn"

@responses.activate
def test_routing_condition_not_met_escalation(unmatched_order_xml):
    soap_fault_response = """<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
  <soapenv:Body>
    <soapenv:Fault>
      <faultcode>soapenv:Server</faultcode>
      <faultstring>Escalation: RoutingConditionNotMet</faultstring>
      <detail>
        <ErrorCode>RoutingConditionNotMet</ErrorCode>
      </detail>
    </soapenv:Fault>
  </soapenv:Body>
</soapenv:Envelope>"""
    responses.add(
        responses.POST,
        ENDPOINT_URL,
        body=soap_fault_response,
        status=500,
        content_type="text/xml; charset=utf-8",
        headers={"SAP_MessageProcessingLogID": "AF_TEST_LOG_003"}
    )

    headers = {"Content-Type": "text/xml; charset=utf-8"}
    response = requests.post(ENDPOINT_URL, data=unmatched_order_xml, headers=headers, timeout=10)

    assert response.status_code == 500
    root = ET.fromstring(response.text)
    fault = root.find(f".//{SOAP_ENV}Fault")
    assert fault is not None, "SOAP Fault element must be present in response"

    faultcode = fault.find("faultcode")
    assert faultcode is not None and "Server" in faultcode.text

    faultstring = fault.find("faultstring")
    assert faultstring is not None and "RoutingConditionNotMet" in faultstring.text

    detail = fault.find("detail")
    assert detail is not None
    err_code = detail.find("ErrorCode")
    assert err_code is not None and err_code.text == "RoutingConditionNotMet"

@responses.activate
def test_exception_subprocess_triggered():
    malformed_body = "<soapenv:Envelope><soapenv:Body><malformed>" 
    error_response = """<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
  <soapenv:Body>
    <soapenv:Fault>
      <faultcode>soapenv:Client</faultcode>
      <faultstring>Error End Event triggered in SubProcess_91</faultstring>
    </soapenv:Fault>
  </soapenv:Body>
</soapenv:Envelope>"""
    responses.add(
        responses.POST,
        ENDPOINT_URL,
        body=error_response,
        status=500,
        content_type="text/xml; charset=utf-8",
        headers={
            "SAP_MessageProcessingLogID": "AF_TEST_LOG_004",
            "X-Exception-Logged": "true",
            "X-Attachment-Created": "Payload"
        }
    )

    response = requests.post(ENDPOINT_URL, data=malformed_body, headers={"Content-Type": "text/xml"}, timeout=10)
    assert response.status_code == 500
    assert response.headers.get("X-Attachment-Created") == "Payload"
    assert response.headers.get("X-Exception-Logged") == "true"

    root = ET.fromstring(response.text)
    fault = root.find(f".//{SOAP_ENV}Fault")
    assert fault is not None
    faultstring = fault.find("faultstring")
    assert faultstring is not None and "SubProcess_91" in faultstring.text
