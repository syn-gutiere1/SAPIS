import json
import xml.etree.ElementTree as ET
import pytest
import requests
import responses

SIMULATED_CPI_ENDPOINT = "https://example.invalid/cxf/Glovia_SalesOrder"

NAMESPACES = {
    "SOAP": "http://schemas.xmlsoap.org/soap/envelope/",
    "ns1": "http://soap.sforce.com/2005/09/outbound",
    "ns2": "urn:sobject.enterprise.soap.sforce.com"
}


@pytest.fixture
def headers_setup():
    return {
        "Content-Type": "text/xml; charset=UTF-8",
        "SOAPAction": "\"\"",
        "Authorization": "Bearer mock_valid_token"
    }


@pytest.fixture
def valid_sales_order_xml():
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<SOAP:Envelope xmlns:SOAP="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:ns1="http://soap.sforce.com/2005/09/outbound" '
        'xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">'
        '<SOAP:Header/>'
        '<SOAP:Body>'
        '<ns1:notifications>'
        '<ns1:OrganizationId>00D50000000XXXXEAA</ns1:OrganizationId>'
        '<ns1:Notification>'
        '<ns1:Id>04v50000000XXXXEAA</ns1:Id>'
        '<ns1:sObject>'
        '<ns2:Id>80150000000XXXXEAA</ns2:Id>'
        '<ns2:giic_OrderTypeCode__c>YORB</ns2:giic_OrderTypeCode__c>'
        '<ns2:OrderNumber>ORD-2026-9901</ns2:OrderNumber>'
        '</ns1:sObject>'
        '</ns1:Notification>'
        '</ns1:notifications>'
        '</SOAP:Body>'
        '</SOAP:Envelope>'
    )


@pytest.fixture
def valid_sales_return_xml():
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<SOAP:Envelope xmlns:SOAP="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:ns1="http://soap.sforce.com/2005/09/outbound" '
        'xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">'
        '<SOAP:Header/>'
        '<SOAP:Body>'
        '<ns1:notifications>'
        '<ns1:Notification>'
        '<ns1:sObject>'
        '<ns2:Id>80150000000XXXXEAB</ns2:Id>'
        '<ns2:giic_OrderTypeCode__c>POR</ns2:giic_OrderTypeCode__c>'
        '</ns1:sObject>'
        '</ns1:Notification>'
        '</ns1:notifications>'
        '</SOAP:Body>'
        '</SOAP:Envelope>'
    )


@pytest.fixture
def unmatched_order_xml():
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<SOAP:Envelope xmlns:SOAP="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:ns1="http://soap.sforce.com/2005/09/outbound" '
        'xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">'
        '<SOAP:Header/>'
        '<SOAP:Body>'
        '<ns1:notifications>'
        '<ns1:Notification>'
        '<ns1:sObject>'
        '<ns2:giic_OrderTypeCode__c>UNKNOWN_NONEXISTENT</ns2:giic_OrderTypeCode__c>'
        '</ns1:sObject>'
        '</ns1:Notification>'
        '</ns1:notifications>'
        '</SOAP:Body>'
        '</SOAP:Envelope>'
    )


@pytest.fixture
def malformed_xml():
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<SOAP:Envelope xmlns:SOAP="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:ns1="http://soap.sforce.com/2005/09/outbound" '
        'xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">'
        '<SOAP:Body>'
        '<ns1:notifications><ns1:Notification><ns1:sObject>'
        '<ns2:giic_OrderTypeCode__c>YORB</ns2:giic_OrderTypeCode__c>'
        '<BrokenTag>Unclosed'
        '</SOAP:Envelope>'
    )


def evaluate_mock_cpi_flow(request_body, request_headers):
    auth = request_headers.get("Authorization", "")
    if not auth or "invalid" in auth:
        return 401, {"Content-Type": "text/xml"}, "<soap:Fault><faultcode>soap:Client</faultcode><faultstring>Unauthorized</faultstring></soap:Fault>"

    if len(request_body.encode("utf-8")) > (40 * 1024 * 1024):
        return 500, {"Content-Type": "text/xml"}, "<soap:Fault><faultcode>soap:Server</faultcode><faultstring>Maximum body size 40MB exceeded</faultstring></soap:Fault>"

    try:
        root = ET.fromstring(request_body)
    except ET.ParseError as e:
        # SubProcess_91 execution simulation
        resp_headers = {
            "Content-Type": "text/xml",
            "X-SAP-Error-Subprocess": "SubProcess_91",
            "X-SAP-InputPayload-Preserved": "true",
            "X-SAP-Logged-Attachment": "Payload"
        }
        return 500, resp_headers, f"<soap:Fault><faultcode>soap:Server</faultcode><faultstring>Mapping/Parsing Error: {str(e)}</faultstring></soap:Fault>"

    order_type_elem = root.find(".//ns2:giic_OrderTypeCode__c", NAMESPACES)
    order_code = order_type_elem.text if order_type_elem is not None else ""

    # Simulate CallActivity_4 property and header assignment
    headers_assigned = {
        "Region": "NA",
        "Country": "US",
        "BusinessObject": "SalesOrder",
        "Sender": "Glovia",
        "TimeStamp": "2026-10-14T10:00:00Z"
    }

    # Router ExclusiveGateway_80 condition checks
    if order_code in ["YORB", "YICB", "MNZ", "PLM"]:
        # Route SalesOrder -> CallActivity_102 -> CallActivity_13 -> EndEvent_2
        resp_headers = {
            "Content-Type": "text/xml",
            "X-SAP-Route-Taken": "SalesOrder",
            "X-SAP-End-Event": "EndEvent_2",
            "X-SAP-CustomHeader-Country": headers_assigned["Country"],
            "X-SAP-CustomHeader-Region": headers_assigned["Region"],
            "X-SAP-CustomHeader-BusinessObject": headers_assigned["BusinessObject"],
            "X-SAP-CustomHeader-Sender": headers_assigned["Sender"]
        }
        return 202, resp_headers, "<Ack><Status>Processed_SalesOrder</Status></Ack>"

    elif order_code == "POR":
        # Route ReturnSalesOrder -> CallActivity_71 -> EndEvent_9
        resp_headers = {
            "Content-Type": "text/xml",
            "X-SAP-Route-Taken": "SO Return create",
            "X-SAP-End-Event": "EndEvent_9",
            "X-SAP-CustomHeader-Country": headers_assigned["Country"],
            "X-SAP-CustomHeader-Region": headers_assigned["Region"],
            "X-SAP-CustomHeader-BusinessObject": headers_assigned["BusinessObject"],
            "X-SAP-CustomHeader-Sender": headers_assigned["Sender"]
        }
        return 202, resp_headers, "<Ack><Status>Processed_ReturnSalesOrder</Status></Ack>"

    else:
        # Default branch SequenceFlow_110 -> EndEvent_11 (EscalationEndEvent)
        resp_headers = {
            "Content-Type": "text/xml",
            "X-SAP-Route-Taken": "Default",
            "X-SAP-End-Event": "EndEvent_11",
            "X-SAP-Escalation-Code": "RoutingConditionNotMet"
        }
        return 422, resp_headers, "<Escalation><ErrorCode>RoutingConditionNotMet</ErrorCode></Escalation>"


@responses.activate
def test_tc_so_01_sales_order_happy_path(valid_sales_order_xml, headers_setup):
    def callback(request):
        status, resp_headers, body = evaluate_mock_cpi_flow(request.body, request.headers)
        return (status, resp_headers, body)

    responses.add_callback(
        responses.POST,
        SIMULATED_CPI_ENDPOINT,
        callback=callback,
        content_type="text/xml"
    )

    session = requests.Session()
    response = session.post(SIMULATED_CPI_ENDPOINT, data=valid_sales_order_xml, headers=headers_setup)

    assert response.status_code == 202
    assert response.headers.get("X-SAP-Route-Taken") == "SalesOrder"
    assert response.headers.get("X-SAP-End-Event") == "EndEvent_2"
    assert response.headers.get("X-SAP-CustomHeader-BusinessObject") == "SalesOrder"
    assert response.headers.get("X-SAP-CustomHeader-Sender") == "Glovia"
    assert "<Status>Processed_SalesOrder</Status>" in response.text


@responses.activate
def test_tc_sr_02_sales_return_happy_path(valid_sales_return_xml, headers_setup):
    def callback(request):
        status, resp_headers, body = evaluate_mock_cpi_flow(request.body, request.headers)
        return (status, resp_headers, body)

    responses.add_callback(
        responses.POST,
        SIMULATED_CPI_ENDPOINT,
        callback=callback,
        content_type="text/xml"
    )

    session = requests.Session()
    response = session.post(SIMULATED_CPI_ENDPOINT, data=valid_sales_return_xml, headers=headers_setup)

    assert response.status_code == 202
    assert response.headers.get("X-SAP-Route-Taken") == "SO Return create"
    assert response.headers.get("X-SAP-End-Event") == "EndEvent_9"
    assert response.headers.get("X-SAP-CustomHeader-Country") == "US"
    assert "<Status>Processed_ReturnSalesOrder</Status>" in response.text


@responses.activate
def test_tc_esc_03_unmatched_routing_escalation(unmatched_order_xml, headers_setup):
    def callback(request):
        status, resp_headers, body = evaluate_mock_cpi_flow(request.body, request.headers)
        return (status, resp_headers, body)

    responses.add_callback(
        responses.POST,
        SIMULATED_CPI_ENDPOINT,
        callback=callback,
        content_type="text/xml"
    )

    session = requests.Session()
    response = session.post(SIMULATED_CPI_ENDPOINT, data=unmatched_order_xml, headers=headers_setup)

    assert response.status_code == 422
    assert response.headers.get("X-SAP-Route-Taken") == "Default"
    assert response.headers.get("X-SAP-End-Event") == "EndEvent_11"
    assert response.headers.get("X-SAP-Escalation-Code") == "RoutingConditionNotMet"
    assert "RoutingConditionNotMet" in response.text


@responses.activate
def test_tc_err_04_mapping_parsing_failure_subprocess(malformed_xml, headers_setup):
    def callback(request):
        status, resp_headers, body = evaluate_mock_cpi_flow(request.body, request.headers)
        return (status, resp_headers, body)

    responses.add_callback(
        responses.POST,
        SIMULATED_CPI_ENDPOINT,
        callback=callback,
        content_type="text/xml"
    )

    session = requests.Session()
    response = session.post(SIMULATED_CPI_ENDPOINT, data=malformed_xml, headers=headers_setup)

    assert response.status_code == 500
    assert response.headers.get("X-SAP-Error-Subprocess") == "SubProcess_91"
    assert response.headers.get("X-SAP-InputPayload-Preserved") == "true"
    assert response.headers.get("X-SAP-Logged-Attachment") == "Payload"
    assert "Mapping/Parsing Error" in response.text


@responses.activate
def test_tc_sec_05_authorization_failure(valid_sales_order_xml):
    unauthorized_headers = {
        "Content-Type": "text/xml; charset=UTF-8",
        "Authorization": "Bearer invalid_expired_token"
    }

    def callback(request):
        status, resp_headers, body = evaluate_mock_cpi_flow(request.body, request.headers)
        return (status, resp_headers, body)

    responses.add_callback(
        responses.POST,
        SIMULATED_CPI_ENDPOINT,
        callback=callback,
        content_type="text/xml"
    )

    session = requests.Session()
    response = session.post(SIMULATED_CPI_ENDPOINT, data=valid_sales_order_xml, headers=unauthorized_headers)

    assert response.status_code == 401
    assert "Unauthorized" in response.text


@responses.activate
def test_tc_bnd_06_payload_size_limit_exceeded(headers_setup):
    oversized_payload = "<SOAP:Envelope>" + ("A" * (41 * 1024 * 1024)) + "</SOAP:Envelope>"

    def callback(request):
        status, resp_headers, body = evaluate_mock_cpi_flow(request.body, request.headers)
        return (status, resp_headers, body)

    responses.add_callback(
        responses.POST,
        SIMULATED_CPI_ENDPOINT,
        callback=callback,
        content_type="text/xml"
    )

    session = requests.Session()
    response = session.post(SIMULATED_CPI_ENDPOINT, data=oversized_payload, headers=headers_setup)

    assert response.status_code == 500
    assert "Maximum body size 40MB exceeded" in response.text
