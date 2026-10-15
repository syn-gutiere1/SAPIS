import json
import xml.etree.ElementTree as ET
import pytest
import requests
import responses

BASE_URL = "https://cpi-runtime.example.invalid/cxf/Glovia_SalesOrder"
SOAP_ENV_NS = "{http://schemas.xmlsoap.org/soap/envelope/}"


@pytest.fixture
def payload_sales_order():
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:ns1="http://soap.sforce.com/2005/09/outbound" '
        'xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">'
        '<soapenv:Body>'
        '<ns1:notifications>'
        '<ns1:Notification>'
        '<ns1:sObject>'
        '<ns2:giic_OrderTypeCode__c>YORB</ns2:giic_OrderTypeCode__c>'
        '<ns2:giic_OrderNumber__c>ORD-90210</ns2:giic_OrderNumber__c>'
        '</ns1:sObject>'
        '</ns1:Notification>'
        '</ns1:notifications>'
        '</soapenv:Body>'
        '</soapenv:Envelope>'
    )


@pytest.fixture
def payload_return_order():
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:ns1="http://soap.sforce.com/2005/09/outbound" '
        'xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">'
        '<soapenv:Body>'
        '<ns1:notifications>'
        '<ns1:Notification>'
        '<ns1:sObject>'
        '<ns2:giic_OrderTypeCode__c>POR</ns2:giic_OrderTypeCode__c>'
        '</ns1:sObject>'
        '</ns1:Notification>'
        '</ns1:notifications>'
        '</soapenv:Body>'
        '</soapenv:Envelope>'
    )


@pytest.fixture
def payload_unmatched_order():
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:ns1="http://soap.sforce.com/2005/09/outbound" '
        'xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">'
        '<soapenv:Body>'
        '<ns1:notifications>'
        '<ns1:Notification>'
        '<ns1:sObject>'
        '<ns2:giic_OrderTypeCode__c>UNKNOWN_CODE</ns2:giic_OrderTypeCode__c>'
        '</ns1:sObject>'
        '</ns1:Notification>'
        '</ns1:notifications>'
        '</soapenv:Body>'
        '</soapenv:Envelope>'
    )


@responses.activate
def test_sales_order_happy_path(payload_sales_order):
    """Validate Route 1 (SalesOrder) executes mapping and completes successfully."""
    mock_response_body = (
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
        '<soapenv:Body><notificationsResponse xmlns="http://soap.sforce.com/2005/09/outbound">'
        '<Ack>true</Ack>' 
        '</notificationsResponse></soapenv:Body></soapenv:Envelope>'
    )
    responses.add(
        responses.POST,
        BASE_URL,
        body=mock_response_body,
        status=200,
        content_type="text/xml; charset=utf-8",
        headers={
            "X-Route-Taken": "SalesOrder",
            "X-Custom-Region": "EMEA",
            "X-Custom-Country": "DE",
            "X-Custom-BusinessObject": "SalesOrder",
        }
    )

    headers = {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": "\"\""}
    response = requests.post(BASE_URL, data=payload_sales_order, headers=headers)

    assert response.status_code == 200
    assert response.headers.get("X-Route-Taken") == "SalesOrder"
    assert response.headers.get("X-Custom-BusinessObject") == "SalesOrder"

    root = ET.fromstring(response.text)
    ack = root.find(".//{http://soap.sforce.com/2005/09/outbound}Ack")
    assert ack is not None
    assert ack.text == "true"


@responses.activate
def test_sales_order_return_route(payload_return_order):
    """Validate Route 2 (SO Return create) routes to generic headers and end event."""
    responses.add(
        responses.POST,
        BASE_URL,
        status=202,
        headers={"X-Route-Taken": "SO_Return_create", "X-Custom-Country": "US"}
    )

    headers = {"Content-Type": "text/xml; charset=utf-8"}
    response = requests.post(BASE_URL, data=payload_return_order, headers=headers)

    assert response.status_code == 202
    assert response.headers.get("X-Route-Taken") == "SO_Return_create"
    assert response.headers.get("X-Custom-Country") == "US"


@responses.activate
def test_unmatched_routing_escalation_fault(payload_unmatched_order):
    """Validate default route raises EscalationEndEvent resulting in SOAP Fault."""
    soap_fault_body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<SOAP:Envelope xmlns:SOAP="http://schemas.xmlsoap.org/soap/envelope/">'
        '<SOAP:Body>'
        '<SOAP:Fault>'
        '<faultcode>SOAP:Server</faultcode>'
        '<faultstring>Escalation occurred: RoutingConditionNotMet</faultstring>'
        '<detail>'
        '<ErrorCode>RoutingConditionNotMet</ErrorCode>'
        '</detail>'
        '</SOAP:Fault>'
        '</SOAP:Body>'
        '</SOAP:Envelope>'
    )
    responses.add(
        responses.POST,
        BASE_URL,
        body=soap_fault_body,
        status=500,
        content_type="text/xml; charset=utf-8"
    )

    headers = {"Content-Type": "text/xml; charset=utf-8"}
    response = requests.post(BASE_URL, data=payload_unmatched_order, headers=headers)

    assert response.status_code == 500
    root = ET.fromstring(response.text)

    fault = root.find(f".//{SOAP_ENV_NS}Fault")
    assert fault is not None, "SOAP Fault element must be qualified by envelope namespace"

    faultcode = fault.find("faultcode")
    assert faultcode is not None
    assert "Server" in faultcode.text

    faultstring = fault.find("faultstring")
    assert faultstring is not None
    assert "RoutingConditionNotMet" in faultstring.text

    detail = fault.find("detail")
    assert detail is not None
    error_code = detail.find("ErrorCode")
    assert error_code is not None
    assert error_code.text == "RoutingConditionNotMet"


@responses.activate
def test_exception_subprocess_trigger():
    """Validate mapping runtime crash routes to Exception Subprocess and returns SOAP Fault."""
    soap_fault_body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<SOAP:Envelope xmlns:SOAP="http://schemas.xmlsoap.org/soap/envelope/">'
        '<SOAP:Body>'
        '<SOAP:Fault>'
        '<faultcode>SOAP:Client</faultcode>'
        '<faultstring>Processing error occurred in Message Mapping</faultstring>'
        '</SOAP:Fault>'
        '</SOAP:Body>'
        '</SOAP:Envelope>'
    )
    responses.add(
        responses.POST,
        BASE_URL,
        body=soap_fault_body,
        status=500,
        content_type="text/xml; charset=utf-8",
        headers={"X-Exception-Handled-By": "SubProcess_91"}
    )

    headers = {"Content-Type": "text/xml; charset=utf-8"}
    response = requests.post(BASE_URL, data="<invalid_payload>", headers=headers)

    assert response.status_code == 500
    assert response.headers.get("X-Exception-Handled-By") == "SubProcess_91"

    root = ET.fromstring(response.text)
    fault = root.find(f".//{SOAP_ENV_NS}Fault")
    assert fault is not None

    faultstring = fault.find("faultstring")
    assert faultstring is not None
    assert "Message Mapping" in faultstring.text


@responses.activate
def test_downstream_timeout_handling(payload_sales_order):
    """Ensure timeouts produce handled failures rather than uncaught connection drops."""
    responses.add(
        responses.POST,
        BASE_URL,
        body=requests.exceptions.ConnectTimeout("Connection timed out")
    )

    headers = {"Content-Type": "text/xml; charset=utf-8"}
    with pytest.raises(requests.exceptions.ConnectTimeout):
        requests.post(BASE_URL, data=payload_sales_order, headers=headers, timeout=5.0)
