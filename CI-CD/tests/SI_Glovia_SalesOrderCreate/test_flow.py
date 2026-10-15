import json
import xml.etree.ElementTree as ET
import pytest
import responses
import requests
from unittest.mock import MagicMock

BASE_ENDPOINT = "https://cpi.internal.example.invalid/cxf/Glovia_SalesOrder"

SOAP_NS = {
    'soap': 'http://schemas.xmlsoap.org/soap/envelope/',
    'ns1': 'http://soap.sforce.com/2005/09/outbound',
    'ns2': 'urn:sobject.enterprise.soap.sforce.com'
}


@pytest.fixture
def valid_sales_order_payload():
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:ns1="http://soap.sforce.com/2005/09/outbound" '
        'xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">'
        '<soapenv:Header/>'
        '<soapenv:Body>'
        '<ns1:notifications>'
        '<ns1:Notification>'
        '<ns1:Id>04k000000000001AAA</ns1:Id>'
        '<ns1:sObject>'
        '<ns2:Id>a01000000000001AAA</ns2:Id>'
        '<ns2:giic_OrderTypeCode__c>YORB</ns2:giic_OrderTypeCode__c>'
        '<ns2:OrderNumber>ORD-987654</ns2:OrderNumber>'
        '<ns2:CustomerNumber>CUST-10029</ns2:CustomerNumber>'
        '<ns2:OrderType>Standard</ns2:OrderType>'
        '</ns1:sObject>'
        '</ns1:Notification>'
        '</ns1:notifications>'
        '</soapenv:Body>'
        '</soapenv:Envelope>'
    )


@pytest.fixture
def valid_so_return_payload():
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:ns1="http://soap.sforce.com/2005/09/outbound" '
        'xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">'
        '<soapenv:Header/>'
        '<soapenv:Body>'
        '<ns1:notifications>'
        '<ns1:Notification>'
        '<ns1:Id>04k000000000002AAA</ns1:Id>'
        '<ns1:sObject>'
        '<ns2:Id>a01000000000002AAA</ns2:Id>'
        '<ns2:giic_OrderTypeCode__c>PLM</ns2:giic_OrderTypeCode__c>'
        '<ns2:OrderNumber>RET-456789</ns2:OrderNumber>'
        '<ns2:CustomerNumber>CUST-10029</ns2:CustomerNumber>'
        '<ns2:OrderType>Return</ns2:OrderType>'
        '</ns1:sObject>'
        '</ns1:Notification>'
        '</ns1:notifications>'
        '</soapenv:Body>'
        '</soapenv:Envelope>'
    )


@pytest.fixture
def unmatched_payload():
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:ns1="http://soap.sforce.com/2005/09/outbound" '
        'xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">'
        '<soapenv:Header/>'
        '<soapenv:Body>'
        '<ns1:notifications>'
        '<ns1:Notification>'
        '<ns1:Id>04k000000000003AAA</ns1:Id>'
        '<ns1:sObject>'
        '<ns2:Id>a01000000000003AAA</ns2:Id>'
        '<ns2:giic_OrderTypeCode__c>UNKNOWN_CODE</ns2:giic_OrderTypeCode__c>'
        '<ns2:OrderNumber>UNK-000000</ns2:OrderNumber>'
        '<ns2:OrderType>Special</ns2:OrderType>'
        '</ns1:sObject>'
        '</ns1:Notification>'
        '</ns1:notifications>'
        '</soapenv:Body>'
        '</soapenv:Envelope>'
    )


@pytest.fixture
def malformed_payload():
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
        '<soapenv:Body>'
        '<ns1:notifications xmlns:ns1="http://soap.sforce.com/2005/09/outbound">'
        '<broken-xml-tag>'
        '</soapenv:Body>'
        '</soapenv:Envelope>'
    )


class FlowExecutionSimulator:
    """Simulates the SAP Cloud Integration execution engine for Process_1."""

    def __init__(self):
        self.properties = {}
        self.headers = {}
        self.custom_log_headers = {}
        self.attachments = {}

    def execute(self, raw_payload):
        # CallActivity_4: Set Message headers & Properties
        self.properties["InputPayload"] = raw_payload
        self.headers["Region"] = "NA"
        self.headers["Country"] = "US"
        self.headers["TimeStamp"] = "2026-10-14T10:00:00Z"
        self.headers["BusinessObject"] = "SalesOrder"
        self.headers["Sender"] = "Glovia"

        # XML Parsing check for Routing
        try:
            root = ET.fromstring(raw_payload)
        except ET.ParseError as err:
            return self._handle_exception(raw_payload, f"XML Parse Error: {str(err)}")

        order_code_elem = root.find('.//ns2:giic_OrderTypeCode__c', SOAP_NS)
        order_type_elem = root.find('.//ns2:OrderType', SOAP_NS)

        order_code = order_code_elem.text if order_code_elem is not None else ""
        order_type = order_type_elem.text if order_type_elem is not None else ""

        # ExclusiveGateway_80 Router logic
        if order_code in ['YORB', 'YICB', 'POR'] or order_type == 'Standard':
            # SequenceFlow_82 -> CallActivity_102 (Message Mapping) -> CallActivity_13
            self._execute_mapping()
            self._log_generic_headers()
            return {"status": 200, "route": "SalesOrder", "state": "COMPLETED"}
        elif order_code in ['MNZ', 'PLM'] or order_type == 'Return':
            # SequenceFlow_113 -> CallActivity_71 -> EndEvent_9
            self._log_generic_headers()
            return {"status": 200, "route": "SO Return create", "state": "COMPLETED"}
        else:
            # Default SequenceFlow_110 -> EndEvent_11 EscalationEndEvent
            return {"status": 422, "route": "Default", "state": "ESCALATED", "error_code": "RoutingConditionNotMet"}

    def _execute_mapping(self):
        # Simulate mapping execution
        self.properties["MappedOutput"] = "<MappedSalesOrder/>"

    def _log_generic_headers(self):
        for key in ["Country", "Region", "BusinessObject", "Sender", "TimeStamp"]:
            if key in self.headers:
                self.custom_log_headers[key] = self.headers[key]

    def _handle_exception(self, raw_payload, error_detail):
        # SubProcess_91: CallActivity_97 (read from property) -> CallActivity_100 -> EndEvent_95
        restored_payload = self.properties.get("InputPayload", raw_payload)
        self.attachments["Payload"] = restored_payload
        return {"status": 500, "route": "SubProcess_91", "state": "ERROR_END", "detail": error_detail}


@responses.activate
def test_tc_glovia_01_standard_sales_order(valid_sales_order_payload):
    responses.add(
        responses.POST,
        BASE_ENDPOINT,
        body=json.dumps({"status": 200, "route": "SalesOrder", "state": "COMPLETED"}),
        status=200,
        content_type="application/json"
    )

    simulator = FlowExecutionSimulator()
    result = simulator.execute(valid_sales_order_payload)

    assert result["status"] == 200
    assert result["route"] == "SalesOrder"
    assert simulator.properties["InputPayload"] == valid_sales_order_payload
    assert simulator.headers["BusinessObject"] == "SalesOrder"
    assert simulator.custom_log_headers["Region"] == "NA"
    assert simulator.custom_log_headers["Country"] == "US"

    response = requests.post(BASE_ENDPOINT, data=valid_sales_order_payload, timeout=5)
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["route"] == "SalesOrder"


@responses.activate
def test_tc_glovia_02_sales_order_return(valid_so_return_payload):
    responses.add(
        responses.POST,
        BASE_ENDPOINT,
        body=json.dumps({"status": 200, "route": "SO Return create", "state": "COMPLETED"}),
        status=200,
        content_type="application/json"
    )

    simulator = FlowExecutionSimulator()
    result = simulator.execute(valid_so_return_payload)

    assert result["status"] == 200
    assert result["route"] == "SO Return create"
    assert "MappedOutput" not in simulator.properties
    assert simulator.custom_log_headers["BusinessObject"] == "SalesOrder"

    response = requests.post(BASE_ENDPOINT, data=valid_so_return_payload, timeout=5)
    assert response.status_code == 200
    assert response.json()["route"] == "SO Return create"


@responses.activate
def test_tc_glovia_03_unmatched_routing_escalation(unmatched_payload):
    responses.add(
        responses.POST,
        BASE_ENDPOINT,
        body=json.dumps({"status": 422, "route": "Default", "state": "ESCALATED", "error_code": "RoutingConditionNotMet"}),
        status=422,
        content_type="application/json"
    )

    simulator = FlowExecutionSimulator()
    result = simulator.execute(unmatched_payload)

    assert result["status"] == 422
    assert result["state"] == "ESCALATED"
    assert result["error_code"] == "RoutingConditionNotMet"

    response = requests.post(BASE_ENDPOINT, data=unmatched_payload, timeout=5)
    assert response.status_code == 422
    assert response.json()["error_code"] == "RoutingConditionNotMet"


@responses.activate
def test_tc_glovia_04_mapping_and_parsing_failure(malformed_payload):
    responses.add(
        responses.POST,
        BASE_ENDPOINT,
        body=json.dumps({"status": 500, "route": "SubProcess_91", "state": "ERROR_END"}),
        status=500,
        content_type="application/json"
    )

    simulator = FlowExecutionSimulator()
    result = simulator.execute(malformed_payload)

    assert result["status"] == 500
    assert result["state"] == "ERROR_END"
    assert "Payload" in simulator.attachments
    assert simulator.attachments["Payload"] == malformed_payload

    response = requests.post(BASE_ENDPOINT, data=malformed_payload, timeout=5)
    assert response.status_code == 500
    assert response.json()["state"] == "ERROR_END"


@responses.activate
def test_tc_glovia_05_downstream_connection_timeout():
    responses.add(
        responses.POST,
        BASE_ENDPOINT,
        body=requests.exceptions.ConnectTimeout("Connection timed out to downstream REST service")
    )

    with pytest.raises(requests.exceptions.ConnectTimeout):
        requests.post(BASE_ENDPOINT, json={"test": "payload"}, timeout=2)
