import json
import xml.etree.ElementTree as ET
import pytest
import requests
import responses

SOAP_ENV = "{http://schemas.xmlsoap.org/soap/envelope/}"
BASE_URL = "https://example.invalid/cxf/Glovia_SalesOrder"


@pytest.fixture
def payload_sales_order():
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<SOAP:Envelope xmlns:SOAP=\"http://schemas.xmlsoap.org/soap/envelope/\" "
        "xmlns:ns1=\"http://soap.sforce.com/2005/09/outbound\" "
        "xmlns:ns2=\"urn:sobject.enterprise.soap.sforce.com\">"
        "<SOAP:Header/>"
        "<SOAP:Body>"
        "<ns1:notifications>"
        "<ns1:Notification>"
        "<ns1:Id>04k000000000001AAA</ns1:Id>"
        "<ns1:sObject>"
        "<ns2:Id>a0B000000000001EAA</ns2:Id>"
        "<ns2:giic_OrderTypeCode__c>YORB</ns2:giic_OrderTypeCode__c>"
        "<ns2:giic_OrderNumber__c>ORD-998811</ns2:giic_OrderNumber__c>"
        "</ns1:sObject>"
        "</ns1:Notification>"
        "</ns1:notifications>"
        "</SOAP:Body>"
        "</SOAP:Envelope>"
    )


@pytest.fixture
def payload_return_order():
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<SOAP:Envelope xmlns:SOAP=\"http://schemas.xmlsoap.org/soap/envelope/\" "
        "xmlns:ns1=\"http://soap.sforce.com/2005/09/outbound\" "
        "xmlns:ns2=\"urn:sobject.enterprise.soap.sforce.com\">"
        "<SOAP:Header/>"
        "<SOAP:Body>"
        "<ns1:notifications>"
        "<ns1:Notification>"
        "<ns1:Id>04k000000000002AAA</ns1:Id>"
        "<ns1:sObject>"
        "<ns2:Id>a0B000000000002EAA</ns2:Id>"
        "<ns2:giic_OrderTypeCode__c>POR</ns2:giic_OrderTypeCode__c>"
        "</ns1:sObject>"
        "</ns1:Notification>"
        "</ns1:notifications>"
        "</SOAP:Body>"
        "</SOAP:Envelope>"
    )


@pytest.fixture
def payload_unmatched_order():
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<SOAP:Envelope xmlns:SOAP=\"http://schemas.xmlsoap.org/soap/envelope/\" "
        "xmlns:ns1=\"http://soap.sforce.com/2005/09/outbound\" "
        "xmlns:ns2=\"urn:sobject.enterprise.soap.sforce.com\">"
        "<SOAP:Header/>"
        "<SOAP:Body>"
        "<ns1:notifications>"
        "<ns1:Notification>"
        "<ns1:Id>04k000000000003AAA</ns1:Id>"
        "<ns1:sObject>"
        "<ns2:giic_OrderTypeCode__c>UNKNOWN_VAL</ns2:giic_OrderTypeCode__c>"
        "</ns1:sObject>"
        "</ns1:Notification>"
        "</ns1:notifications>"
        "</SOAP:Body>"
        "</SOAP:Envelope>"
    )


@responses.activate
def test_sales_order_happy_path(payload_sales_order):
    responses.add(
        responses.POST,
        BASE_URL,
        body="<SOAP:Envelope xmlns:SOAP=\"http://schemas.xmlsoap.org/soap/envelope/\"><SOAP:Body><Response>Success</Response></SOAP:Body></SOAP:Envelope>",
        status=200,
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SAP_MessageProcessingLogID": "AF_001_SALES_ORDER",
            "Region": "EMEA",
            "Country": "DE",
            "BusinessObject": "SalesOrder",
            "Sender": "Glovia"
        }
    )

    response = requests.post(
        BASE_URL,
        data=payload_sales_order,
        headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": "\"\""}
    )

    assert response.status_code == 200
    assert response.headers["BusinessObject"] == "SalesOrder"
    assert response.headers["Sender"] == "Glovia"
    assert response.headers["SAP_MessageProcessingLogID"] == "AF_001_SALES_ORDER"

    root = ET.fromstring(response.text)
    body = root.find(f"{SOAP_ENV}Body")
    assert body is not None
    assert body.find("Response").text == "Success"


@responses.activate
def test_return_order_route(payload_return_order):
    responses.add(
        responses.POST,
        BASE_URL,
        body="<SOAP:Envelope xmlns:SOAP=\"http://schemas.xmlsoap.org/soap/envelope/\"><SOAP:Body><ReturnAck>Processed</ReturnAck></SOAP:Body></SOAP:Envelope>",
        status=200,
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "BusinessObject": "SOReturn",
            "Country": "US"
        }
    )

    response = requests.post(
        BASE_URL,
        data=payload_return_order,
        headers={"Content-Type": "text/xml; charset=utf-8"}
    )

    assert response.status_code == 200
    assert response.headers.get("BusinessObject") == "SOReturn"
    root = ET.fromstring(response.text)
    body = root.find(f"{SOAP_ENV}Body")
    assert body is not None
    assert body.find("ReturnAck").text == "Processed"


@responses.activate
def test_escalation_end_routing_condition_not_met(payload_unmatched_order):
    escalation_soap_fault = (
        "<SOAP:Envelope xmlns:SOAP=\"http://schemas.xmlsoap.org/soap/envelope/\">"
        "<SOAP:Body>"
        "<SOAP:Fault>"
        "<faultcode>SOAP:Server</faultcode>"
        "<faultstring>Escalation End: RoutingConditionNotMet</faultstring>"
        "<detail>"
        "<ErrorCode>RoutingConditionNotMet</ErrorCode>"
        "<ErrorMessage>No routing condition matched for provided OrderTypeCode</ErrorMessage>"
        "</detail>"
        "</SOAP:Fault>"
        "</SOAP:Body>"
        "</SOAP:Envelope>"
    )

    responses.add(
        responses.POST,
        BASE_URL,
        body=escalation_soap_fault,
        status=500,
        headers={"Content-Type": "text/xml; charset=utf-8"}
    )

    response = requests.post(
        BASE_URL,
        data=payload_unmatched_order,
        headers={"Content-Type": "text/xml; charset=utf-8"}
    )

    assert response.status_code == 500
    root = ET.fromstring(response.text)

    fault = root.find(f".//{SOAP_ENV}Fault")
    assert fault is not None, "SOAP Fault must be present in response"

    fault_string = fault.find("faultstring")
    assert fault_string is not None
    assert "RoutingConditionNotMet" in fault_string.text

    fault_code = fault.find("faultcode")
    assert fault_code is not None
    assert "SOAP:Server" in fault_code.text

    detail = fault.find("detail")
    assert detail is not None
    error_code = detail.find("ErrorCode")
    assert error_code is not None and error_code.text == "RoutingConditionNotMet"


@responses.activate
def test_exception_subprocess_error_end():
    soap_fault_response = (
        "<SOAP:Envelope xmlns:SOAP=\"http://schemas.xmlsoap.org/soap/envelope/\">"
        "<SOAP:Body>"
        "<SOAP:Fault>"
        "<faultcode>SOAP:Server</faultcode>"
        "<faultstring>MappingException in CallActivity_102</faultstring>"
        "<detail>"
        "<LogPayloadAttachment>Captured</LogPayloadAttachment>"
        "</detail>"
        "</SOAP:Fault>"
        "</SOAP:Body>"
        "</SOAP:Envelope>"
    )

    responses.add(
        responses.POST,
        BASE_URL,
        body=soap_fault_response,
        status=500,
        headers={"Content-Type": "text/xml; charset=utf-8"}
    )

    corrupt_data = "<SOAP:Envelope xmlns:SOAP=\"http://schemas.xmlsoap.org/soap/envelope/\"><SOAP:Body><Corrupt/></SOAP:Body></SOAP:Envelope>"
    response = requests.post(
        BASE_URL,
        data=corrupt_data,
        headers={"Content-Type": "text/xml; charset=utf-8"}
    )

    assert response.status_code == 500
    root = ET.fromstring(response.text)
    fault = root.find(f".//{SOAP_ENV}Fault")
    assert fault is not None

    fault_string = fault.find("faultstring")
    assert fault_string is not None and "MappingException" in fault_string.text

    detail = fault.find("detail")
    assert detail is not None
    log_att = detail.find("LogPayloadAttachment")
    assert log_att is not None and log_att.text == "Captured"


@responses.activate
def test_malformed_xml_adapter_level_fault():
    soap_fault_cxf = (
        "<SOAP:Envelope xmlns:SOAP=\"http://schemas.xmlsoap.org/soap/envelope/\">"
        "<SOAP:Body>"
        "<SOAP:Fault>"
        "<faultcode>SOAP:Client</faultcode>"
        "<faultstring>XML parse error has occurred: unclosed token</faultstring>"
        "</SOAP:Fault>"
        "</SOAP:Body>"
        "</SOAP:Envelope>"
    )

    responses.add(
        responses.POST,
        BASE_URL,
        body=soap_fault_cxf,
        status=500,
        headers={"Content-Type": "text/xml; charset=utf-8"}
    )

    response = requests.post(
        BASE_URL,
        data="<SOAP:Envelope><SOAP:Body><unclosed>",
        headers={"Content-Type": "text/xml; charset=utf-8"}
    )

    assert response.status_code == 500
    root = ET.fromstring(response.text)
    fault = root.find(f".//{SOAP_ENV}Fault")
    assert fault is not None
    fault_string = fault.find("faultstring")
    assert fault_string is not None and "XML parse error" in fault_string.text
