import json
import xml.etree.ElementTree as ET
import pytest
import responses
import requests

SOAP_ENV_NS = "{http://schemas.xmlsoap.org/soap/envelope/}"
ENDPOINT_URL = "https://cpi-runtime.example.invalid/cxf/Glovia_SalesOrder"

SO_VALID_PAYLOAD = """<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:out="http://soap.sforce.com/2005/09/outbound" xmlns:sobj="urn:sobject.enterprise.soap.sforce.com">
  <soapenv:Header/>
  <soapenv:Body>
    <out:notifications>
      <out:Notification>
        <out:sObject>
          <sobj:giic_OrderTypeCode__c>YORB</sobj:giic_OrderTypeCode__c>
          <sobj:giic_OrderNumber__c>ORD-99201</sobj:giic_OrderNumber__c>
        </out:sObject>
      </out:Notification>
    </out:notifications>
  </soapenv:Body>
</soapenv:Envelope>"""

SO_RETURN_PAYLOAD = """<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:out="http://soap.sforce.com/2005/09/outbound" xmlns:sobj="urn:sobject.enterprise.soap.sforce.com">
  <soapenv:Header/>
  <soapenv:Body>
    <out:notifications>
      <out:Notification>
        <out:sObject>
          <sobj:giic_OrderTypeCode__c>POR</sobj:giic_OrderTypeCode__c>
        </out:sObject>
      </out:Notification>
    </out:notifications>
  </soapenv:Body>
</soapenv:Envelope>"""

SO_UNMATCHED_PAYLOAD = """<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:out="http://soap.sforce.com/2005/09/outbound" xmlns:sobj="urn:sobject.enterprise.soap.sforce.com">
  <soapenv:Header/>
  <soapenv:Body>
    <out:notifications>
      <out:Notification>
        <out:sObject>
          <sobj:giic_OrderTypeCode__c>UNKNOWN_CODE</sobj:giic_OrderTypeCode__c>
        </out:sObject>
      </out:Notification>
    </out:notifications>
  </soapenv:Body>
</soapenv:Envelope>"""

SO_MALFORMED_PAYLOAD = "<soapenv:Envelope><soapenv:Body><unclosedTag>Malformed" 

SOAP_ACK_SUCCESS = """<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
  <soapenv:Body>
    <notificationsResponse xmlns="http://soap.sforce.com/2005/09/outbound">
      <Ack>true</Ack>
    </notificationsResponse>
  </soapenv:Body>
</soapenv:Envelope>"""

SOAP_ESCALATION_FAULT = """<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
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

SOAP_PARSING_FAULT = """<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
  <soapenv:Body>
    <soapenv:Fault>
      <faultcode>soapenv:Client</faultcode>
      <faultstring>XML parsing error: unclosed token</faultstring>
    </soapenv:Fault>
  </soapenv:Body>
</soapenv:Envelope>"""


@pytest.fixture
def standard_headers():
    return {
        "Content-Type": "text/xml; charset=UTF-8",
        "SOAPAction": """""",
        "Region": "NA",
        "Country": "US",
        "BusinessObject": "SalesOrder",
        "Sender": "Glovia"
    }


@responses.activate
def test_tc_so_create_happy_path(standard_headers):
    responses.add(
        responses.POST,
        ENDPOINT_URL,
        body=SOAP_ACK_SUCCESS,
        status=200,
        content_type="text/xml; charset=UTF-8",
        headers={"X-Flow-Step": "EndEvent_2"}
    )

    response = requests.post(ENDPOINT_URL, data=SO_VALID_PAYLOAD, headers=standard_headers, timeout=10)
    
    assert response.status_code == 200
    assert response.headers.get("X-Flow-Step") == "EndEvent_2"
    
    root = ET.fromstring(response.text)
    fault = root.find(f".//{SOAP_ENV_NS}Fault")
    assert fault is None, "Happy path response must not contain a SOAP Fault"
    
    ack = root.find(".//{http://soap.sforce.com/2005/09/outbound}Ack")
    assert ack is not None and ack.text == "true"


@responses.activate
def test_tc_so_return_happy_path(standard_headers):
    responses.add(
        responses.POST,
        ENDPOINT_URL,
        body=SOAP_ACK_SUCCESS,
        status=200,
        content_type="text/xml; charset=UTF-8",
        headers={"X-Flow-Step": "EndEvent_9"}
    )

    response = requests.post(ENDPOINT_URL, data=SO_RETURN_PAYLOAD, headers=standard_headers, timeout=10)
    
    assert response.status_code == 200
    assert response.headers.get("X-Flow-Step") == "EndEvent_9"
    
    root = ET.fromstring(response.text)
    ack = root.find(".//{http://soap.sforce.com/2005/09/outbound}Ack")
    assert ack is not None and ack.text == "true"


@responses.activate
def test_tc_unmatched_routing_escalation(standard_headers):
    responses.add(
        responses.POST,
        ENDPOINT_URL,
        body=SOAP_ESCALATION_FAULT,
        status=500,
        content_type="text/xml; charset=UTF-8"
    )

    response = requests.post(ENDPOINT_URL, data=SO_UNMATCHED_PAYLOAD, headers=standard_headers, timeout=10)
    
    assert response.status_code == 500
    root = ET.fromstring(response.text)
    
    fault = root.find(f".//{SOAP_ENV_NS}Fault")
    assert fault is not None, "Expected SOAP Fault element qualified by Envelope namespace"
    
    # In SOAP 1.1 faultcode and faultstring are UNQUALIFIED
    faultcode = fault.find("faultcode")
    assert faultcode is not None and "Server" in faultcode.text
    
    faultstring = fault.find("faultstring")
    assert faultstring is not None and "RoutingConditionNotMet" in faultstring.text
    
    detail = fault.find("detail")
    assert detail is not None
    err_code = detail.find("ErrorCode")
    assert err_code is not None and err_code.text == "RoutingConditionNotMet"


@responses.activate
def test_tc_malformed_xml_handling(standard_headers):
    responses.add(
        responses.POST,
        ENDPOINT_URL,
        body=SOAP_PARSING_FAULT,
        status=500,
        content_type="text/xml; charset=UTF-8"
    )

    response = requests.post(ENDPOINT_URL, data=SO_MALFORMED_PAYLOAD, headers=standard_headers, timeout=10)
    
    assert response.status_code == 500
    root = ET.fromstring(response.text)
    
    fault = root.find(f".//{SOAP_ENV_NS}Fault")
    assert fault is not None
    
    faultstring = fault.find("faultstring")
    assert faultstring is not None and "XML parsing error" in faultstring.text


@responses.activate
def test_tc_connection_timeout_handling(standard_headers):
    responses.add(
        responses.POST,
        ENDPOINT_URL,
        body=requests.exceptions.ConnectTimeout("Connection timed out")
    )

    with pytest.raises(requests.exceptions.ConnectTimeout):
        requests.post(ENDPOINT_URL, data=SO_VALID_PAYLOAD, headers=standard_headers, timeout=5)
