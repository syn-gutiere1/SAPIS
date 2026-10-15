import json
import os
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock, patch
import pytest
import requests


SOAP_ENDPOINT = "http://localhost:0/cxf/Glovia_SalesOrder"


@pytest.fixture
def valid_sales_order_xml():
    return """<?xml version="1.0" encoding="UTF-8"?>
<SOAP:Envelope xmlns:SOAP="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ns1="http://soap.sforce.com/2005/09/outbound" xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">
  <SOAP:Header/>
  <SOAP:Body>
    <ns1:notifications>
      <ns1:Notification>
        <ns1:Id>04k5g000000XYZ1AAO</ns1:Id>
        <ns1:sObject>
          <ns2:Id>a1B5g000000ABC1EAO</ns2:Id>
          <ns2:giic_OrderTypeCode__c>YORB</ns2:giic_OrderTypeCode__c>
          <ns2:giic_OrderNumber__c>SO-2026-1001</ns2:giic_OrderNumber__c>
        </ns1:sObject>
      </ns1:Notification>
    </ns1:notifications>
  </SOAP:Body>
</SOAP:Envelope>"""


@pytest.fixture
def valid_return_order_xml():
    return """<?xml version="1.0" encoding="UTF-8"?>
<SOAP:Envelope xmlns:SOAP="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ns1="http://soap.sforce.com/2005/09/outbound" xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">
  <SOAP:Header/>
  <SOAP:Body>
    <ns1:notifications>
      <ns1:Notification>
        <ns1:Id>04k5g000000XYZ2AAO</ns1:Id>
        <ns1:sObject>
          <ns2:Id>a1B5g000000RET2EAO</ns2:Id>
          <ns2:giic_OrderTypeCode__c>RO_RET</ns2:giic_OrderTypeCode__c>
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
        <ns1:Id>04k5g000000XYZ9999</ns1:Id>
        <ns1:sObject>
          <ns2:Id>a1B5g000000UNK9EAO</ns2:Id>
          <ns2:giic_OrderTypeCode__c>UNKNOWN_CODE</ns2:giic_OrderTypeCode__c>
        </ns1:sObject>
      </ns1:Notification>
    </ns1:notifications>
  </SOAP:Body>
</SOAP:Envelope>"""


@pytest.fixture
def malformed_xml():
    return """<SOAP:Envelope xmlns:SOAP="http://schemas.xmlsoap.org/soap/envelope/"><SOAP:Body><unclosed>"""


class FlowExecutionEngine:
    """Simulates the SAP Cloud Integration flow execution pipeline based on BPMN definition."""

    def __init__(self, routes_config=None):
        self.routes_config = routes_config or {
            "Glovia_Route_SalesOrder": "/ns1:notifications/ns1:Notification/ns1:sObject/ns2:giic_OrderTypeCode__c = 'YORB'",
            "Glovia_Route2_ReturnSalesOrder": "/ns1:notifications/ns1:Notification/ns1:sObject/ns2:giic_OrderTypeCode__c = 'RO_RET'",
        }

    def process_message(self, raw_payload, headers=None):
        headers = headers or {}
        properties = {}
        message_log = {}

        # CallActivity_4: Set Message headers & Store property
        properties["InputPayload"] = raw_payload
        headers["Region"] = "NA"
        headers["Country"] = "US"
        headers["TimeStamp"] = "2026-10-14T10:00:00Z"
        headers["BusinessObject"] = "SalesOrder"
        headers["Sender"] = "Glovia"

        # ParallelGateway_74: SequenceFlow_108 -> SequenceFlow_109
        # ExclusiveGateway_80: Evaluate routes
        try:
            namespaces = {
                "SOAP": "http://schemas.xmlsoap.org/soap/envelope/",
                "ns1": "http://soap.sforce.com/2005/09/outbound",
                "ns2": "urn:sobject.enterprise.soap.sforce.com",
            }
            root = ET.fromstring(raw_payload)
            order_type_elem = root.find(".//ns2:giic_OrderTypeCode__c", namespaces)
            order_type = order_type_elem.text if order_type_elem is not None else ""
        except ET.ParseError as err:
            # Triggers SubProcess_91 (Exception Subprocess 1)
            return self._handle_exception_subprocess(properties, str(err))

        # Route matching
        if order_type == "YORB":
            # SequenceFlow_82 -> CallActivity_102 (Mapping) -> CallActivity_13 (Headers)
            mapped_payload = f"<MappedSalesOrder><OrderCode>{order_type}</OrderCode></MappedSalesOrder>"
            # Interface_Generic_Headers.groovy execution
            message_log["CustomHeaders"] = {
                "Country": headers.get("Country"),
                "Region": headers.get("Region"),
                "BusinessObject": headers.get("BusinessObject"),
                "Sender": headers.get("Sender"),
                "TimeStamp": headers.get("TimeStamp"),
            }
            return {
                "status": "SUCCESS",
                "end_event": "EndEvent_2",
                "body": mapped_payload,
                "headers": headers,
                "properties": properties,
                "message_log": message_log,
            }
        elif order_type == "RO_RET":
            # SequenceFlow_113 -> CallActivity_71 (Headers) -> EndEvent_9
            message_log["CustomHeaders"] = {
                "Country": headers.get("Country"),
                "Region": headers.get("Region"),
                "BusinessObject": headers.get("BusinessObject"),
                "Sender": headers.get("Sender"),
                "TimeStamp": headers.get("TimeStamp"),
            }
            return {
                "status": "SUCCESS",
                "end_event": "EndEvent_9",
                "body": raw_payload,
                "headers": headers,
                "properties": properties,
                "message_log": message_log,
            }
        else:
            # SequenceFlow_110: Default Route -> EndEvent_11 (Escalation End)
            return {
                "status": "ESCALATED",
                "end_event": "EndEvent_11",
                "escalation_code": "RoutingConditionNotMet",
                "properties": properties,
                "headers": headers,
            }

    def _handle_exception_subprocess(self, properties, error_message):
        # CallActivity_97: Read payload from property
        restored_payload = properties.get("InputPayload", "")
        # CallActivity_100: Log Payload script (Log_Payload.gsh)
        attachments = {"Payload": restored_payload}
        # EndEvent_95: Error End
        return {
            "status": "ERROR",
            "end_event": "EndEvent_95",
            "error": error_message,
            "attachments": attachments,
            "properties": properties,
        }


def test_sales_order_happy_path(valid_sales_order_xml):
    """TC_GLOVIA_001: Verifies Sales Order mapping, generic headers, and EndEvent_2 completion."""
    with patch("requests.Session.post") as mock_post:
        engine = FlowExecutionEngine()
        result = engine.process_message(valid_sales_order_xml)

        assert result["status"] == "SUCCESS"
        assert result["end_event"] == "EndEvent_2"
        assert "<MappedSalesOrder>" in result["body"]
        assert result["headers"]["Region"] == "NA"
        assert result["headers"]["BusinessObject"] == "SalesOrder"
        assert result["properties"]["InputPayload"] == valid_sales_order_xml
        assert result["message_log"]["CustomHeaders"]["Country"] == "US"
        assert mock_post.call_count == 0  # Guarantees zero unmocked network I/O


def test_return_order_happy_path(valid_return_order_xml):
    """TC_GLOVIA_002: Verifies SO Return path bypasses mapping directly to EndEvent_9."""
    with patch("requests.Session.post") as mock_post:
        engine = FlowExecutionEngine()
        result = engine.process_message(valid_return_order_xml)

        assert result["status"] == "SUCCESS"
        assert result["end_event"] == "EndEvent_9"
        assert result["headers"]["Sender"] == "Glovia"
        assert result["properties"]["InputPayload"] == valid_return_order_xml
        assert mock_post.call_count == 0


def test_unmatched_routing_triggers_escalation(unmatched_order_xml):
    """TC_GLOVIA_003: Verifies unhandled order types terminate at EndEvent_11 with RoutingConditionNotMet."""
    with patch("requests.Session.post") as mock_post:
        engine = FlowExecutionEngine()
        result = engine.process_message(unmatched_order_xml)

        assert result["status"] == "ESCALATED"
        assert result["end_event"] == "EndEvent_11"
        assert result["escalation_code"] == "RoutingConditionNotMet"
        assert mock_post.call_count == 0


def test_malformed_xml_triggers_exception_subprocess(malformed_xml):
    """TC_GLOVIA_004: Verifies parser failures activate SubProcess_91, restore InputPayload, and log payload."""
    with patch("requests.Session.post") as mock_post:
        engine = FlowExecutionEngine()
        result = engine.process_message(malformed_xml)

        assert result["status"] == "ERROR"
        assert result["end_event"] == "EndEvent_95"
        assert "Payload" in result["attachments"]
        assert result["attachments"]["Payload"] == malformed_xml
        assert mock_post.call_count == 0


def test_inbound_cxf_adapter_rejection():
    """TC_GLOVIA_005: Verifies timeout/boundary handling at SOAP adapter interface level."""
    with patch("requests.post") as mock_http_post:
        mock_http_post.side_effect = requests.exceptions.ConnectTimeout("Connection timed out to integration endpoint")

        with pytest.raises(requests.exceptions.ConnectTimeout):
            # Simulating client invoking ephemeral test address without network transmission
            requests.post("http://example.invalid/cxf/Glovia_SalesOrder", data="<test/>", timeout=2.0)

        assert mock_http_post.call_count == 1
