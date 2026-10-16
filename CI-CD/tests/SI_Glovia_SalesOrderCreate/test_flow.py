import json
import xml.etree.ElementTree as ET
import pytest
import responses
import requests

BASE_ENDPOINT = "http://localhost.example.invalid/cxf/Glovia_SalesOrder"
SOAP_ENV = "{http://schemas.xmlsoap.org/soap/envelope/}"


@pytest.fixture
def sales_order_xml():
    return """<?xml version="1.0" encoding="UTF-8"?>
<SOAP:Envelope xmlns:SOAP="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ns1="http://soap.sforce.com/2005/09/outbound" xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">
  <SOAP:Header/>
  <SOAP:Body>
    <ns1:notifications>
      <ns1:OrganizationId>00D000000000001EAA</ns1:OrganizationId>
      <ns1:Notification>
        <ns1:sObject>
          <ns2:giic_OrderTypeCode__c>YORB</ns2:giic_OrderTypeCode__c>
          <ns2:OrderNumber__c>SO-2023-99881</ns2:OrderNumber__c>
        </ns1:sObject>
      </ns1:Notification>
    </ns1:notifications>
  </SOAP:Body>
</SOAP:Envelope>"""


@pytest.fixture
def so_return_xml():
    return """<?xml version="1.0" encoding="UTF-8"?>
<SOAP:Envelope xmlns:SOAP="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ns1="http://soap.sforce.com/2005/09/outbound" xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">
  <SOAP:Header/>
  <SOAP:Body>
    <ns1:notifications>
      <ns1:Notification>
        <ns1:sObject>
          <ns2:giic_OrderTypeCode__c>POR</ns2:giic_OrderTypeCode__c>
          <ns2:ReturnOrderNumber__c>RET-2023-00412</ns2:ReturnOrderNumber__c>
        </ns1:sObject>
      </ns1:Notification>
    </ns1:notifications>
  </SOAP:Body>
</SOAP:Envelope>"""


@pytest.fixture
def unmatched_order_xml():
    return """<?xml version="1.0" encoding="UTF-8"?>
<SOAP:Envelope xmlns:SOAP="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ns1="http://soap.sforce.com/2005/09/outbound" xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">
  <SOAP:Header/>
  <SOAP:Body>
    <ns1:notifications>
      <ns1:Notification>
        <ns1:sObject>
          <ns2:giic_OrderTypeCode__c>UNKNOWN_CODE</ns2:giic_OrderTypeCode__c>
        </ns1:sObject>
      </ns1:Notification>
    </ns1:notifications>
  </SOAP:Body>
</SOAP:Envelope>"""


@pytest.fixture
def soap_fault_xml():
    return """<?xml version="1.0" encoding="UTF-8"?>
<SOAP:Envelope xmlns:SOAP="http://schemas.xmlsoap.org/soap/envelope/">
  <SOAP:Body>
    <SOAP:Fault>
      <faultcode>SOAP:Client</faultcode>
      <faultstring>RoutingConditionNotMet</faultstring>
      <faultactor>ExclusiveGateway_80</faultactor>
      <detail>
        <errorDetail>No route evaluated to true</errorDetail>
      </detail>
    </SOAP:Fault>
  </SOAP:Body>
</SOAP:Envelope>"""


@responses.activate
def test_sales_order_happy_path(sales_order_xml):
    responses.add(
        responses.POST,
        BASE_ENDPOINT,
        status=202,
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SAP-Processing-Route": "SequenceFlow_82",
            "SAP-Header-Region": "EMEA",
            "SAP-Header-Country": "DE",
            "SAP-Header-BusinessObject": "SalesOrder",
            "SAP-Header-Sender": "Glovia",
        },
        body="<response><status>Accepted</status></response>",
    )

    headers = {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": ""}
    resp = requests.post(BASE_ENDPOINT, data=sales_order_xml, headers=headers, timeout=5)

    assert resp.status_code == 202
    assert resp.headers.get("SAP-Processing-Route") == "SequenceFlow_82"
    assert resp.headers.get("SAP-Header-BusinessObject") == "SalesOrder"
    assert resp.headers.get("SAP-Header-Sender") == "Glovia"


@responses.activate
def test_so_return_happy_path(so_return_xml):
    responses.add(
        responses.POST,
        BASE_ENDPOINT,
        status=202,
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SAP-Processing-Route": "SequenceFlow_113",
            "SAP-Header-BusinessObject": "SOReturn",
            "SAP-Header-Sender": "Glovia",
        },
        body="<response><status>ProcessedReturn</status></response>",
    )

    resp = requests.post(BASE_ENDPOINT, data=so_return_xml, timeout=5)
    assert resp.status_code == 202
    assert resp.headers.get("SAP-Processing-Route") == "SequenceFlow_113"
    assert resp.headers.get("SAP-Header-BusinessObject") == "SOReturn"


@responses.activate
def test_router_escalation_default_route(unmatched_order_xml, soap_fault_xml):
    responses.add(
        responses.POST,
        BASE_ENDPOINT,
        status=500,
        headers={"Content-Type": "text/xml; charset=utf-8"},
        body=soap_fault_xml,
    )

    resp = requests.post(BASE_ENDPOINT, data=unmatched_order_xml, timeout=5)
    assert resp.status_code == 500

    root = ET.fromstring(resp.text)
    fault = root.find(f".//{SOAP_ENV}Fault")
    assert fault is not None

    faultcode = fault.find("faultcode")
    assert faultcode is not None and "SOAP:Client" in faultcode.text

    faultstring = fault.find("faultstring")
    assert faultstring is not None and "RoutingConditionNotMet" in faultstring.text

    detail = fault.find("detail")
    assert detail is not None and detail.find("errorDetail") is not None


@responses.activate
def test_exception_subprocess_restores_original_payload(sales_order_xml):
    responses.add(
        responses.POST,
        BASE_ENDPOINT,
        status=500,
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SAP-Error-Handler": "SubProcess_91",
            "SAP-Restored-Payload": "InputPayload",
            "SAP-Payload-Logged": "true",
        },
        body="""<?xml version="1.0" encoding="UTF-8"?>
<SOAP:Envelope xmlns:SOAP="http://schemas.xmlsoap.org/soap/envelope/">
  <SOAP:Body>
    <SOAP:Fault>
      <faultcode>SOAP:Server</faultcode>
      <faultstring>MappingException: Test_Mapping failed</faultstring>
    </SOAP:Fault>
  </SOAP:Body>
</SOAP:Envelope>""",
    )

    resp = requests.post(BASE_ENDPOINT, data=sales_order_xml, timeout=5)
    assert resp.status_code == 500
    assert resp.headers.get("SAP-Error-Handler") == "SubProcess_91"
    assert resp.headers.get("SAP-Restored-Payload") == "InputPayload"
    assert resp.headers.get("SAP-Payload-Logged") == "true"

    root = ET.fromstring(resp.text)
    fault = root.find(f".//{SOAP_ENV}Fault")
    assert fault is not None
    faultstring = fault.find("faultstring")
    assert faultstring is not None and "MappingException" in faultstring.text


@responses.activate
def test_adapter_timeout_handling():
    responses.add(
        responses.POST,
        BASE_ENDPOINT,
        body=requests.exceptions.ConnectTimeout("Connection timed out"),
    )

    with pytest.raises(requests.exceptions.ConnectTimeout):
        requests.post(BASE_ENDPOINT, data="<xml/>", timeout=2)


@responses.activate
def test_header_enricher_null_safe_handling(sales_order_xml):
    responses.add(
        responses.POST,
        BASE_ENDPOINT,
        status=202,
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SAP-Header-Sender": "Glovia",
        },
        body="<response><status>Accepted</status></response>",
    )

    resp = requests.post(BASE_ENDPOINT, data=sales_order_xml, timeout=5)
    assert resp.status_code == 202
    assert "SAP-Header-Country" not in resp.headers
    assert resp.headers.get("SAP-Header-Sender") == "Glovia"
