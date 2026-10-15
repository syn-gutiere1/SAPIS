import json
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock
import pytest
import responses

# Namespace mappings extracted from BPMN definitions
NAMESPACES = {
    'soapenv': 'http://schemas.xmlsoap.org/soap/envelope/',
    'ns1': 'http://soap.sforce.com/2005/09/outbound',
    'ns2': 'urn:sobject.enterprise.soap.sforce.com'
}

# Simulated flow runtime context representing SAP Cloud Integration Exchange
class CPIExchange:
    def __init__(self, body, headers=None, properties=None):
        self.body = body
        self.headers = headers or {}
        self.properties = properties or {}
        self.custom_header_properties = {}
        self.attachments = {}
        self.escalated = False
        self.escalation_code = None
        self.error = None
        self.end_event = None

    def add_custom_header_property(self, name, value):
        self.custom_header_properties[name] = value

    def add_attachment_as_string(self, title, content, content_type):
        self.attachments[title] = {'content': content, 'type': content_type}


def simulate_call_activity_4_enricher(exchange, config):
    """Simulates CallActivity_4: Set Message headers and property InputPayload."""
    exchange.properties['InputPayload'] = exchange.body
    exchange.headers['Region'] = config.get('Region', 'EMEA')
    exchange.headers['Country'] = config.get('Country', 'DE')
    exchange.headers['TimeStamp'] = config.get('TimeStamp', '2026-10-14T10:00:00Z')
    exchange.headers['BusinessObject'] = config.get('BusinessObject', 'SalesOrder')
    exchange.headers['Sender'] = config.get('Sender', 'Glovia')


def evaluate_router(exchange, config):
    """Simulates ExclusiveGateway_80 routing logic based on XML content and configured conditions."""
    try:
        root = ET.fromstring(exchange.body)
        order_type_elem = root.find('.//ns1:Notification/ns1:sObject/ns2:giic_OrderTypeCode__c', NAMESPACES)
        order_type = order_type_elem.text if order_type_elem is not None else ''
    except Exception as exc:
        raise ValueError(f"XML parsing error during routing: {exc}")

    # Evaluate route matching rules configured for flow parameters
    so_route_codes = config.get('Glovia_Route_SalesOrder_Codes', ['YORB', 'YICB', 'POR', 'MNZ', 'PLM'])
    return_route_codes = config.get('Glovia_Route2_ReturnSalesOrder_Codes', ['RET', 'SRT'])

    if order_type in so_route_codes:
        return 'SequenceFlow_82'
    elif order_type in return_route_codes:
        return 'SequenceFlow_113'
    else:
        return 'SequenceFlow_110'  # Default branch


def simulate_interface_generic_headers(exchange):
    """Simulates Interface_Generic_Headers.groovy execution logging headers to custom properties."""
    for key in ['Country', 'Region', 'BusinessObject', 'Sender', 'Receiver', 'TimeStamp']:
        val = exchange.headers.get(key)
        if val is not None:
            exchange.add_custom_header_property(key, val)


def simulate_message_mapping_1(exchange):
    """Simulates CallActivity_102 Test_Mapping.mmap."""
    root = ET.fromstring(exchange.body)
    so_name = root.find('.//ns1:Notification/ns1:sObject/ns2:Name', NAMESPACES)
    if so_name is None or not so_name.text:
        raise ValueError("Mapping Error: Field 'Name' is required by Test_Mapping.mmap")
    transformed = f"<CanonicalOrder><OrderId>{so_name.text}</OrderId></CanonicalOrder>"
    exchange.body = transformed


def simulate_exception_subprocess(exchange, error):
    """Simulates SubProcess_91: reads InputPayload property, logs attachment, and ends in Error End."""
    exchange.error = error
    # CallActivity_97: Enricher wrapContent ${property.InputPayload}
    exchange.body = exchange.properties.get('InputPayload', '')
    # CallActivity_100: Log_Payload (1).gsh
    exchange.add_attachment_as_string('Payload', exchange.body, 'text/plain')
    # EndEvent_95: Error End
    exchange.end_event = 'EndEvent_95'


def run_iflow_simulation(payload, config):
    """Executes full end-to-end integration process graph model."""
    exchange = CPIExchange(body=payload)
    try:
        # StartEvent_2 -> SequenceFlow_3 -> CallActivity_4
        simulate_call_activity_4_enricher(exchange, config)

        # SequenceFlow_108 -> ParallelGateway_74 -> SequenceFlow_109 -> ExclusiveGateway_80
        route = evaluate_router(exchange, config)

        if route == 'SequenceFlow_82':
            # SalesOrder path
            simulate_message_mapping_1(exchange)  # CallActivity_102
            simulate_interface_generic_headers(exchange)  # CallActivity_13
            exchange.end_event = 'EndEvent_2'
        elif route == 'SequenceFlow_113':
            # SO Return path
            simulate_interface_generic_headers(exchange)  # CallActivity_71
            exchange.end_event = 'EndEvent_9'
        elif route == 'SequenceFlow_110':
            # Escalation End
            exchange.escalated = True
            exchange.escalation_code = 'RoutingConditionNotMet'
            exchange.end_event = 'EndEvent_11'
    except Exception as exc:
        simulate_exception_subprocess(exchange, exc)

    return exchange


@pytest.fixture
def flow_config():
    return {
        'Region': 'NA',
        'Country': 'US',
        'TimeStamp': '2026-10-14T12:00:00Z',
        'BusinessObject': 'SalesOrder',
        'Sender': 'Glovia',
        'Glovia_Route_SalesOrder_Codes': ['YORB', 'YICB', 'POR', 'MNZ', 'PLM'],
        'Glovia_Route2_ReturnSalesOrder_Codes': ['RET', 'SRT']
    }


@pytest.fixture
def valid_sales_order_xml():
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:ns1="http://soap.sforce.com/2005/09/outbound" '
        'xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">'
        '<soapenv:Header/><soapenv:Body><ns1:notifications><ns1:Notification>'
        '<ns1:Id>04k000000000001AAA</ns1:Id><ns1:sObject>'
        '<ns2:Id>a01000000000001AAA</ns2:Id>'
        '<ns2:giic_OrderTypeCode__c>YORB</ns2:giic_OrderTypeCode__c>'
        '<ns2:Name>SO-90001234</ns2:Name>'
        '</ns1:sObject></ns1:Notification></ns1:notifications></soapenv:Body></soapenv:Envelope>'
    )


@pytest.fixture
def valid_so_return_xml():
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:ns1="http://soap.sforce.com/2005/09/outbound" '
        'xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">'
        '<soapenv:Header/><soapenv:Body><ns1:notifications><ns1:Notification>'
        '<ns1:Id>04k000000000002BBB</ns1:Id><ns1:sObject>'
        '<ns2:Id>a01000000000002BBB</ns2:Id>'
        '<ns2:giic_OrderTypeCode__c>RET</ns2:giic_OrderTypeCode__c>'
        '<ns2:Name>RET-80005678</ns2:Name>'
        '</ns1:sObject></ns1:Notification></ns1:notifications></soapenv:Body></soapenv:Envelope>'
    )


@pytest.fixture
def unmatched_order_xml():
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:ns1="http://soap.sforce.com/2005/09/outbound" '
        'xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">'
        '<soapenv:Header/><soapenv:Body><ns1:notifications><ns1:Notification>'
        '<ns1:Id>04k000000000003CCC</ns1:Id><ns1:sObject>'
        '<ns2:Id>a01000000000003CCC</ns2:Id>'
        '<ns2:giic_OrderTypeCode__c>UNMATCHED</ns2:giic_OrderTypeCode__c>'
        '<ns2:Name>UNMATCHED-1111</ns2:Name>'
        '</ns1:sObject></ns1:Notification></ns1:notifications></soapenv:Body></soapenv:Envelope>'
    )


@pytest.fixture
def invalid_mapping_xml():
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:ns1="http://soap.sforce.com/2005/09/outbound" '
        'xmlns:ns2="urn:sobject.enterprise.soap.sforce.com">'
        '<soapenv:Header/><soapenv:Body><ns1:notifications><ns1:Notification>'
        '<ns1:Id>04k000000000004DDD</ns1:Id><ns1:sObject>'
        '<ns2:Id>a01000000000004DDD</ns2:Id>'
        '<ns2:giic_OrderTypeCode__c>YORB</ns2:giic_OrderTypeCode__c>'
        '<!-- Missing Name field triggers mapping error -->'
        '</ns1:sObject></ns1:Notification></ns1:notifications></soapenv:Body></soapenv:Envelope>'
    )


@responses.activate
def test_sales_order_happy_path(valid_sales_order_xml, flow_config):
    """Validate TC_GLOVIA_SO_001: Sales order creation path successfully routes and maps."""
    # Verify no outgoing network call takes place without mock interception
    responses.add(responses.POST, 'https://example.invalid/rest/receiver', status=200)

    exchange = run_iflow_simulation(valid_sales_order_xml, flow_config)

    assert exchange.end_event == 'EndEvent_2'
    assert exchange.properties.get('InputPayload') == valid_sales_order_xml
    assert exchange.headers['Region'] == 'NA'
    assert exchange.headers['Sender'] == 'Glovia'
    assert exchange.custom_header_properties['BusinessObject'] == 'SalesOrder'
    assert '<OrderId>SO-90001234</OrderId>' in exchange.body
    assert not exchange.escalated
    assert exchange.error is None


def test_so_return_route(valid_so_return_xml, flow_config):
    """Validate TC_GLOVIA_SO_002: SO Return order routes to EndEvent_9 without mapping."""
    exchange = run_iflow_simulation(valid_so_return_xml, flow_config)

    assert exchange.end_event == 'EndEvent_9'
    assert exchange.properties.get('InputPayload') == valid_so_return_xml
    assert exchange.custom_header_properties['Country'] == 'US'
    assert exchange.custom_header_properties['Region'] == 'NA'
    assert not exchange.escalated
    assert exchange.error is None


def test_unmatched_route_escalation(unmatched_order_xml, flow_config):
    """Validate TC_GLOVIA_SO_003: Unmatched order code falls back to escalation end."""
    exchange = run_iflow_simulation(unmatched_order_xml, flow_config)

    assert exchange.end_event == 'EndEvent_11'
    assert exchange.escalated is True
    assert exchange.escalation_code == 'RoutingConditionNotMet'


def test_mapping_failure_exception_subprocess(invalid_mapping_xml, flow_config):
    """Validate TC_GLOVIA_SO_004: Mapping exception routes to SubProcess_91 and logs payload."""
    exchange = run_iflow_simulation(invalid_mapping_xml, flow_config)

    assert exchange.end_event == 'EndEvent_95'
    assert exchange.error is not None
    assert 'Mapping Error' in str(exchange.error)
    # CallActivity_97 must restore original payload
    assert exchange.body == invalid_mapping_xml
    # CallActivity_100 must attach restored payload
    assert 'Payload' in exchange.attachments
    assert exchange.attachments['Payload']['content'] == invalid_mapping_xml
    assert exchange.attachments['Payload']['type'] == 'text/plain'


@responses.activate
def test_downstream_receiver_connection_failure():
    """Validate TC_GLOVIA_SO_005: Outbound connection errors are caught and handled by SubProcess_91."""
    import requests
    mock_url = 'https://example.invalid/glovia/orders'
    responses.add(responses.POST, mock_url, body=requests.exceptions.ConnectionError('Downstream host unreachable'))

    payload = '<test>data</test>'
    exchange = CPIExchange(body=payload)
    simulate_call_activity_4_enricher(exchange, {})

    try:
        # Attempt downstream communication simulating an external endpoint push
        requests.post(mock_url, data=exchange.body, timeout=5)
    except requests.exceptions.RequestException as exc:
        simulate_exception_subprocess(exchange, exc)

    assert exchange.end_event == 'EndEvent_95'
    assert 'Downstream host unreachable' in str(exchange.error)
    assert exchange.attachments['Payload']['content'] == payload
