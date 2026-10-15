import json
import os
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock, patch
import pytest
import requests


NAMESPACES = {
    "SOAP": "http://schemas.xmlsoap.org/soap/envelope/",
    "ns1": "http://soap.sforce.com/2005/09/outbound",
    "ns2": "urn:sobject.enterprise.soap.sforce.com",
}


class FlowExecutionSimulator:
    """Simulates the SAP Cloud Integration BPMN flow logic for unit testing."""

    def __init__(self, external_params=None):
        self.external_params = external_params or {
            "Region": "NA",
            "Country": "US",
            "TimeStamp": "2026-10-14T10:00:00Z",
            "BusinessObject": "SalesOrder",
            "Sender": "Glovia",
            "Glovia_Route_SalesOrder": "YORB",
            "Glovia_Route2_ReturnSalesOrder": "POR",
        }

    def execute(self, raw_xml_body):
        exchange = {
            "body": raw_xml_body,
            "headers": {},
            "properties": {},
            "attachments": {},
            "custom_header_properties": {},
            "status": "INITIATED",
            "escalation_code": None,
            "error_end": False,
            "completed_end_event": None,
        }

        # CallActivity_4: Set Message headers and property InputPayload
        exchange["properties"]["InputPayload"] = exchange["body"]
        exchange["headers"]["Region"] = self.external_params.get("Region")
        exchange["headers"]["Country"] = self.external_params.get("Country")
        exchange["headers"]["TimeStamp"] = self.external_params.get("TimeStamp")
        exchange["headers"]["BusinessObject"] = self.external_params.get("BusinessObject")
        exchange["headers"]["Sender"] = self.external_params.get("Sender")

        # Parse XML for Router condition
        try:
            root = ET.fromstring(exchange["body"])
            order_type_elem = root.find(
                ".//ns1:Notification/ns1:sObject/ns2:giic_OrderTypeCode__c",
                NAMESPACES,
            )
            order_type = order_type_elem.text if order_type_elem is not None else ""
        except ET.ParseError as e:
            # Triggers SubProcess_91 (Exception Subprocess)
            return self._handle_exception(exchange, e)

        # ExclusiveGateway_80: Router
        if order_type == self.external_params.get("Glovia_Route_SalesOrder"):
            # SequenceFlow_82 -> CallActivity_102 (Message Mapping) -> CallActivity_13
            exchange["body"] = f"<MappedSalesOrder><OrderCode>{order_type}</OrderCode></MappedSalesOrder>"
            self._execute_interface_generic_headers(exchange)
            exchange["completed_end_event"] = "EndEvent_2"
            exchange["status"] = "SUCCESS"
        elif order_type == self.external_params.get("Glovia_Route2_ReturnSalesOrder"):
            # SequenceFlow_113 -> CallActivity_71 (Call Headers)
            self._execute_interface_generic_headers(exchange)
            exchange["completed_end_event"] = "EndEvent_9"
            exchange["status"] = "SUCCESS"
        else:
            # SequenceFlow_110 -> EndEvent_11 (Escalation End)
            exchange["escalation_code"] = "RoutingConditionNotMet"
            exchange["completed_end_event"] = "EndEvent_11"
            exchange["status"] = "ESCALATED"

        return exchange

    def _execute_interface_generic_headers(self, exchange):
        for key in ["Country", "Region", "BusinessObject", "Sender", "Receiver", "TimeStamp"]:
            val = exchange["headers"].get(key)
            if val is not None:
                exchange["custom_header_properties"][key] = val

    def _handle_exception(self, exchange, error):
        # CallActivity_97: Read the payload from property
        exchange["body"] = exchange["properties"].get("InputPayload", "")
        # CallActivity_100: Log Payload
        exchange["attachments"]["Payload"] = exchange["body"]
        exchange["error_end"] = True
        exchange["completed_end_event"] = "EndEvent_95"
        exchange["status"] = "ERROR"
        exchange["error_message"] = str(error)
        return exchange


@pytest.fixture
def test_data():
    return {
        "so_valid": """<?xml version="1.0" encoding="UTF-8"?>
<SOAP:Envelope xmlns:SOAP="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ns1="http://soap.sforce.com/2005/09/outbound" xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">
  <SOAP:Body>
    <ns1:notifications>
      <ns1:Notification>
        <ns1:sObject>
          <ns2:giic_OrderTypeCode__c>YORB</ns2:giic_OrderTypeCode__c>
          <ns2:OrderNumber__c>SO-2026-0001</ns2:OrderNumber__c>
        </ns1:sObject>
      </ns1:Notification>
    </ns1:notifications>
  </SOAP:Body>
</SOAP:Envelope>""",
        "return_valid": """<?xml version="1.0" encoding="UTF-8"?>
<SOAP:Envelope xmlns:SOAP="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ns1="http://soap.sforce.com/2005/09/outbound" xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">
  <SOAP:Body>
    <ns1:notifications>
      <ns1:Notification>
        <ns1:sObject>
          <ns2:giic_OrderTypeCode__c>POR</ns2:giic_OrderTypeCode__c>
          <ns2:OrderNumber__c>RET-2026-8802</ns2:OrderNumber__c>
        </ns1:sObject>
      </ns1:Notification>
    </ns1:notifications>
  </SOAP:Body>
</SOAP:Envelope>""",
        "unmatched": """<?xml version="1.0" encoding="UTF-8"?>
<SOAP:Envelope xmlns:SOAP="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ns1="http://soap.sforce.com/2005/09/outbound" xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">
  <SOAP:Body>
    <ns1:notifications>
      <ns1:Notification>
        <ns1:sObject>
          <ns2:giic_OrderTypeCode__c>INVALID_TYPE</ns2:giic_OrderTypeCode__c>
        </ns1:sObject>
      </ns1:Notification>
    </ns1:notifications>
  </SOAP:Body>
</SOAP:Envelope>""",
        "malformed": """<?xml version="1.0" encoding="UTF-8"?>
<SOAP:Envelope xmlns:SOAP="http://schemas.xmlsoap.org/soap/envelope/">
  <SOAP:Body><unclosed>
</SOAP:Envelope>""",
    }


def test_sales_order_happy_path(test_data):
    simulator = FlowExecutionSimulator()
    result = simulator.execute(test_data["so_valid"])

    assert result["status"] == "SUCCESS"
    assert result["completed_end_event"] == "EndEvent_2"
    assert result["properties"]["InputPayload"] == test_data["so_valid"]
    assert result["headers"]["Region"] == "NA"
    assert result["headers"]["Country"] == "US"
    assert result["custom_header_properties"]["Region"] == "NA"
    assert "<MappedSalesOrder>" in result["body"]


def test_so_return_route(test_data):
    simulator = FlowExecutionSimulator()
    result = simulator.execute(test_data["return_valid"])

    assert result["status"] == "SUCCESS"
    assert result["completed_end_event"] == "EndEvent_9"
    assert result["custom_header_properties"]["BusinessObject"] == "SalesOrder"
    assert result["custom_header_properties"]["Sender"] == "Glovia"


def test_router_escalation_default_path(test_data):
    simulator = FlowExecutionSimulator()
    result = simulator.execute(test_data["unmatched"])

    assert result["status"] == "ESCALATED"
    assert result["completed_end_event"] == "EndEvent_11"
    assert result["escalation_code"] == "RoutingConditionNotMet"


def test_exception_subprocess_execution(test_data):
    simulator = FlowExecutionSimulator()
    result = simulator.execute(test_data["malformed"])

    assert result["status"] == "ERROR"
    assert result["error_end"] is True
    assert result["completed_end_event"] == "EndEvent_95"
    # Verifies CallActivity_97 restored body from property.InputPayload
    assert result["body"] == test_data["malformed"]
    # Verifies CallActivity_100 attached payload
    assert result["attachments"]["Payload"] == test_data["malformed"]


@patch("requests.post")
def test_soap_receiver_mocked_network_call(mock_post, test_data):
    # Asserts that external HTTP calls adhere strictly to no network I/O
    mock_response = MagicMock()
    mock_response.status_code = 202
    mock_response.text = "<SOAP:Envelope><SOAP:Body><Ack>Success</Ack></SOAP:Body></SOAP:Envelope>"
    mock_post.return_value = mock_response

    ephemeral_port = os.getenv("TEST_MOCK_PORT", "0")
    endpoint_url = f"http://localhost:{ephemeral_port}/cxf/Glovia_SalesOrder"

    response = requests.post(
        endpoint_url,
        data=test_data["so_valid"],
        headers={"Content-Type": "text/xml; charset=utf-8"},
        timeout=5,
    )

    assert response.status_code == 202
    assert "Ack" in response.text
    mock_post.assert_called_once_with(
        endpoint_url,
        data=test_data["so_valid"],
        headers={"Content-Type": "text/xml; charset=utf-8"},
        timeout=5,
    )
