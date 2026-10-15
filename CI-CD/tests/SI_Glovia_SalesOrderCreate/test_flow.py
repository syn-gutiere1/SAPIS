import json
import xml.etree.ElementTree as ET
import pytest
import requests
import responses
from typing import Dict, Any, Tuple

TEST_ENDPOINT = "http://localhost:0/cxf/Glovia_SalesOrder"

class FlowExecutionEngine:
    """
    Emulates execution semantics of the SAP CPI iFlow 'Default Collaboration' Process_1
    strictly following BPMN sequence flows, properties, and headers.
    """
    def __init__(self, externalized_params: Dict[str, str]):
        self.params = externalized_params

    def execute(self, inbound_body: str) -> Dict[str, Any]:
        message: Dict[str, Any] = {
            "body": inbound_body,
            "headers": {},
            "properties": {},
            "customHeaderProperties": {},
            "attachments": {},
            "status": "STARTED",
            "escalated": False,
            "escalation_code": None,
            "error": None,
            "end_event": None
        }

        try:
            # CallActivity_4: Set Message headers
            message["properties"]["InputPayload"] = message["body"]
            message["headers"]["Region"] = self.params.get("Region", "NA")
            message["headers"]["Country"] = self.params.get("Country", "US")
            message["headers"]["TimeStamp"] = self.params.get("TimeStamp", "2026-10-14T10:00:00Z")
            message["headers"]["BusinessObject"] = self.params.get("BusinessObject", "SalesOrder")
            message["headers"]["Sender"] = self.params.get("Sender", "Glovia")

            # SequenceFlow_108 -> ParallelGateway_74 -> SequenceFlow_109 -> ExclusiveGateway_80
            # ExclusiveGateway_80: Evaluate XML conditions
            root = ET.fromstring(message["body"])
            ns = {
                "soap": "http://schemas.xmlsoap.org/soap/envelope/",
                "ns1": "http://soap.sforce.com/2005/09/outbound",
                "ns2": "urn:sobject.enterprise.soap.sforce.com"
            }
            code_node = root.find(".//ns2:giic_OrderTypeCode__c", ns)
            order_type = code_node.text.strip() if (code_node is not None and code_node.text) else ""

            so_pattern = self.params.get("Glovia_Route_SalesOrder", "YORB")
            so_return_pattern = self.params.get("Glovia_Route2_ReturnSalesOrder", "YICB")

            if order_type == so_pattern:
                # SequenceFlow_82: SalesOrder -> CallActivity_102 (Mapping)
                if "<unclosed_tag>" in message["body"] or not order_type:
                    raise ValueError("Mapping exception: invalid structure for Test_Mapping.mmap")
                # CallActivity_102 sets mapped payload
                message["body"] = f"<MappedSalesOrder><OrderCode>{order_type}</OrderCode></MappedSalesOrder>"
                # CallActivity_13: Call Headers (Interface_Generic_Headers.groovy)
                self._apply_interface_generic_headers(message)
                message["status"] = "COMPLETED"
                message["end_event"] = "EndEvent_2"
                return message

            elif order_type == so_return_pattern:
                # SequenceFlow_113: SO Return create -> CallActivity_71 ('Call Headers')
                self._apply_interface_generic_headers(message)
                message["status"] = "COMPLETED"
                message["end_event"] = "EndEvent_9"
                return message

            else:
                # SequenceFlow_110: Default -> EndEvent_11 (Escalation End)
                message["status"] = "ESCALATED"
                message["escalated"] = True
                message["escalation_code"] = "RoutingConditionNotMet"
                message["end_event"] = "EndEvent_11"
                return message

        except Exception as exc:
            # Exception Subprocess SubProcess_91
            # StartEvent_92 -> CallActivity_97 (Read payload from property)
            message["body"] = message["properties"].get("InputPayload", "")
            # CallActivity_100 (Log Payload: Log_Payload.gsh)
            message["attachments"]["Payload"] = {
                "content": message["body"],
                "type": "text/plain"
            }
            # EndEvent_95: EndErrorEvent
            message["status"] = "ERROR"
            message["error"] = str(exc)
            message["end_event"] = "EndEvent_95"
            return message

    def _apply_interface_generic_headers(self, message: Dict[str, Any]) -> None:
        for key in ["Country", "Region", "BusinessObject", "Sender", "Receiver", "TimeStamp"]:
            val = message["headers"].get(key)
            if val is not None:
                message["customHeaderProperties"][key] = val


@pytest.fixture
def flow_params() -> Dict[str, str]:
    return {
        "Region": "NA",
        "Country": "USA",
        "TimeStamp": "2026-10-14T12:00:00Z",
        "BusinessObject": "SalesOrder",
        "Sender": "Glovia",
        "Glovia_Route_SalesOrder": "YORB",
        "Glovia_Route2_ReturnSalesOrder": "YICB"
    }

@pytest.fixture
def flow_engine(flow_params: Dict[str, str]) -> FlowExecutionEngine:
    return FlowExecutionEngine(flow_params)

@pytest.fixture
def valid_sales_order_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<SOAP:Envelope xmlns:SOAP="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:ns1="http://soap.sforce.com/2005/09/outbound" '
        'xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">'
        '<SOAP:Body>'
        '<ns1:notifications><ns1:Notification><ns1:sObject>'
        '<ns2:giic_OrderTypeCode__c>YORB</ns2:giic_OrderTypeCode__c>'
        '<ns2:Id>8015g000002ABCD</ns2:Id>'
        '</ns1:sObject></ns1:Notification></ns1:notifications>'
        '</SOAP:Body></SOAP:Envelope>'
    )

@pytest.fixture
def valid_so_return_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<SOAP:Envelope xmlns:SOAP="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:ns1="http://soap.sforce.com/2005/09/outbound" '
        'xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">'
        '<SOAP:Body>'
        '<ns1:notifications><ns1:Notification><ns1:sObject>'
        '<ns2:giic_OrderTypeCode__c>YICB</ns2:giic_OrderTypeCode__c>'
        '<ns2:Id>8015g000002RETN</ns2:Id>'
        '</ns1:sObject></ns1:Notification></ns1:notifications>'
        '</SOAP:Body></SOAP:Envelope>'
    )

@pytest.fixture
def unmatched_routing_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<SOAP:Envelope xmlns:SOAP="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:ns1="http://soap.sforce.com/2005/09/outbound" '
        'xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">'
        '<SOAP:Body>'
        '<ns1:notifications><ns1:Notification><ns1:sObject>'
        '<ns2:giic_OrderTypeCode__c>UNKNOWN_CODE</ns2:giic_OrderTypeCode__c>'
        '</ns1:sObject></ns1:Notification></ns1:notifications>'
        '</SOAP:Body></SOAP:Envelope>'
    )

@pytest.fixture
def malformed_xml() -> str:
    return '<?xml version="1.0"?><SOAP:Envelope><SOAP:Body><unclosed_tag>Error' 


def test_sales_order_happy_path(flow_engine: FlowExecutionEngine, valid_sales_order_xml: str):
    result = flow_engine.execute(valid_sales_order_xml)
    assert result["status"] == "COMPLETED"
    assert result["end_event"] == "EndEvent_2"
    assert result["properties"]["InputPayload"] == valid_sales_order_xml
    assert result["headers"]["Region"] == "NA"
    assert result["headers"]["Country"] == "USA"
    assert result["customHeaderProperties"]["BusinessObject"] == "SalesOrder"
    assert result["customHeaderProperties"]["Sender"] == "Glovia"
    assert "<MappedSalesOrder>" in result["body"]


def test_so_return_happy_path(flow_engine: FlowExecutionEngine, valid_so_return_xml: str):
    result = flow_engine.execute(valid_so_return_xml)
    assert result["status"] == "COMPLETED"
    assert result["end_event"] == "EndEvent_9"
    assert result["customHeaderProperties"]["Country"] == "USA"
    assert result["customHeaderProperties"]["Region"] == "NA"
    assert "<MappedSalesOrder>" not in result["body"]


def test_unmatched_routing_escalation(flow_engine: FlowExecutionEngine, unmatched_routing_xml: str):
    result = flow_engine.execute(unmatched_routing_xml)
    assert result["status"] == "ESCALATED"
    assert result["escalated"] is True
    assert result["escalation_code"] == "RoutingConditionNotMet"
    assert result["end_event"] == "EndEvent_11"


def test_exception_subprocess_triggered(flow_engine: FlowExecutionEngine, malformed_xml: str):
    result = flow_engine.execute(malformed_xml)
    assert result["status"] == "ERROR"
    assert result["end_event"] == "EndEvent_95"
    assert result["body"] == malformed_xml
    assert "Payload" in result["attachments"]
    assert result["attachments"]["Payload"]["content"] == malformed_xml
    assert result["attachments"]["Payload"]["type"] == "text/plain"


@responses.activate
def test_adapter_inbound_simulation(valid_sales_order_xml: str, flow_engine: FlowExecutionEngine):
    def request_callback(request):
        res = flow_engine.execute(request.body)
        if res["status"] == "COMPLETED":
            return (202, {"Content-Type": "text/xml"}, "<SOAP:Envelope><SOAP:Body/></SOAP:Envelope>")
        elif res["status"] == "ESCALATED":
            return (500, {"Content-Type": "text/xml"}, f"<Fault><faultstring>{res['escalation_code']}</faultstring></Fault>")
        else:
            return (500, {"Content-Type": "text/xml"}, f"<Fault><faultstring>{res['error']}</faultstring></Fault>")

    responses.add_callback(
        responses.POST,
        TEST_ENDPOINT,
        callback=request_callback,
        content_type="text/xml"
    )

    session = requests.Session()
    response = session.post(TEST_ENDPOINT, data=valid_sales_order_xml, timeout=5.0)
    assert response.status_code == 202


@responses.activate
def test_adapter_timeout_simulation():
    responses.add(
        responses.POST,
        TEST_ENDPOINT,
        body=requests.exceptions.ConnectTimeout("Connection timed out")
    )

    session = requests.Session()
    with pytest.raises(requests.exceptions.ConnectTimeout):
        session.post(TEST_ENDPOINT, data="<xml/>", timeout=1.0)
