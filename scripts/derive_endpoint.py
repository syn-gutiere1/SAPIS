#!/usr/bin/env python3
"""
derive_endpoint.py - derive the HTTP trigger of an iFlow from its own `.iflw`.

Why this exists: a UAT smoke test needs the full URL to call. The host belongs to
the tenant and must be configured once, but the per-iFlow part - the listener path
- is already declared inside the artifact. Asking a human to type the full URL for
every iFlow would not scale and would drift from the artifact as soon as the flow
is edited.

What is derived from the artifact (verified against a real SAP export):

    <ifl:property><key>ComponentType</key><value>SOAP</value></ifl:property>
    <ifl:property><key>address</key><value>/Glovia_SalesOrder</value></ifl:property>
    <ifl:property><key>TransportProtocol</key><value>HTTP</value></ifl:property>

What can NOT be derived: the tenant host. A `.iflw` records the relative listener
path, never the runtime hostname, because the same artifact is deployable to any
tenant. The host therefore stays a single configured value, shared by every iFlow.

Not every iFlow has an HTTP trigger. A sender adapter of type SFTP, JMS, Mail,
Timer or IDoc is polled or scheduled by the tenant and has no webhook to call. In
that case this script reports `has_http_trigger: false` so the caller can skip the
smoke test with an honest message instead of inventing a URL.

Externalized parameters are a second, subtler case. SAP lets a value be written as
`{{Parameter}}` and supplied by the tenant at deploy time. Such a value is NOT part
of the artifact, so it cannot be derived either - and returning it as if it were the
path produces a URL like `https://tenant.invalid{{Glovia_Address}}`, which nothing
can call. When a placeholder survives in the path or in the component type, the
script reports:

    "unresolved_parameters": ["Glovia_Address"],
    "listener_path": null

so the caller configures the value instead of requesting a nonsense URL. Resolving
`{{Parameter}}` against the tenant is deliberately out of scope: the value lives in
the tenant, not in the artifact, and inventing one would be worse than reporting it.

Output (stdout, JSON):

    {"has_http_trigger": true, "component_type": "SOAP", "path": "/Glovia_SalesOrder",
     "transport_protocol": "HTTP", "listener_path": "/Glovia_SalesOrder"}

Exit codes:
    0  the artifact was read (even when there is no HTTP trigger)
    1  `--fail-if-no-trigger` and the flow has no HTTP trigger
    2  the artifact could not be read, no sender is declared, or a parameter is still
       externalized so no usable path exists
"""

from __future__ import annotations

import argparse
import json
import sys
import re
from pathlib import Path

# Adapter component types that expose an HTTP listener and can therefore be
# triggered by a request. Everything else (SFTP, JMS, Mail, Timer, IDoc,
# SuccessFactors, ...) is polled or scheduled by the tenant.
HTTP_COMPONENT_TYPES = {
    "SOAP",
    "REST",
    "HTTPS",
    "HTTP",
    "ODATA",
    "XI",
    "AS2",
    "IDOC_SOAP",
}

# Properties that may carry the listener path, in order of trustworthiness.
# `urlPath` is used by REST-style adapters; `address` by SOAP and generic HTTP.
PATH_KEYS = ("urlPath", "address", "endpointAddress")

# Property keys that identify the sender adapter block.
SENDER_MARKERS = ("EndpointSender", "Sender")

# SAP externalization: `{{ParameterName}}`. The value is supplied by the tenant at deploy
# time, so it never appears in the artifact and cannot be derived from it.
EXTERNALIZED_PATTERN = re.compile(r"\{\{\s*([A-Za-z0-9_.\-]+)\s*\}\}")


class DeriveError(Exception):
    """The artifact could not be read or declares no usable sender."""


def _parse_properties(xml_text: str) -> list[tuple[str, str]]:
    """
    Extract every `<ifl:property><key>..</key><value>..</value></ifl:property>` pair.

    A tolerant text scan is used on purpose instead of a full XML parse: the
    `.iflw` is BPMN with several namespaces and the property blocks are flat, so a
    regex keeps the dependency surface at zero and does not break on namespace
    changes. The trade-off is documented: this reads properties, it does not
    validate the document.
    """
    pattern = re.compile(
        r"<key>\s*([^<]+?)\s*</key>\s*<value>\s*([^<]*?)\s*</value>",
        re.DOTALL,
    )
    return [(m.group(1), m.group(2)) for m in pattern.finditer(xml_text)]


def _first_property(properties: list[tuple[str, str]], key: str) -> str | None:
    for name, value in properties:
        if name == key and value:
            return value
    return None


def _sender_block(xml_text: str) -> str:
    """
    Return the fragment that describes the sender.

    The sender is identified by the `EndpointSender` participant. When the flow
    declares no such participant there is no entry point at all, which is a real
    condition worth reporting rather than guessing around.
    """
    marker = "EndpointSender"
    index = xml_text.find(marker)
    if index < 0:
        raise DeriveError("no EndpointSender participant found in the artifact")

    # The adapter properties follow the participant declaration; a generous window
    # covers the largest observed sender block without reaching the receivers.
    return xml_text[index : index + 8000]


def derive(iflw_path: Path) -> dict:
    """Derive the HTTP trigger description from a `.iflw` file."""
    if not iflw_path.is_file():
        raise DeriveError(f"artifact not found: {iflw_path}")

    try:
        xml_text = iflw_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise DeriveError(f"could not read {iflw_path}: {exc}") from exc

    sender = _sender_block(xml_text)
    properties = _parse_properties(sender)

    component_type = _first_property(properties, "ComponentType") or ""
    transport = _first_property(properties, "TransportProtocol") or ""

    path = None
    for key in PATH_KEYS:
        path = _first_property(properties, key)
        if path:
            break

    # A parameter that survived as `{{...}}` is a value the tenant owns, not a path. It must
    # not be handed to a caller as if it could be requested.
    unresolved = list(
        dict.fromkeys(
            EXTERNALIZED_PATTERN.findall(f"{path or ''} {component_type}")
        )
    )

    # The component type may itself be externalized, in which case whether this flow has an
    # HTTP trigger is unknown rather than false: it depends on the value the tenant supplies.
    type_is_known = (
        bool(component_type) and not EXTERNALIZED_PATTERN.search(component_type)
    )
    path_is_usable = bool(path) and not EXTERNALIZED_PATTERN.search(path)

    if unresolved:
        has_trigger = None if not type_is_known else (
            path_is_usable and component_type.upper() in HTTP_COMPONENT_TYPES
        )
    else:
        has_trigger = component_type.upper() in HTTP_COMPONENT_TYPES and path_is_usable

    return {
        "iflow": iflw_path.stem,
        "has_http_trigger": has_trigger,
        "component_type": component_type or None,
        "transport_protocol": transport or None,
        "path": path,
        "listener_path": path if has_trigger else None,
        "unresolved_parameters": unresolved,
        "sender_auth_type": _first_property(properties, "senderAuthType"),
        "required_role": _first_property(properties, "userRole"),
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Derive the HTTP trigger (listener path) of an iFlow from its .iflw artifact."
    )
    parser.add_argument("artifact", help="Path to the .iflw file, or to the directory that contains it.")
    parser.add_argument("--json", action="store_true", help="Emit JSON on stdout (default).")
    parser.add_argument(
        "--fail-if-no-trigger",
        action="store_true",
        help="Exit 1 when the flow has no HTTP trigger, instead of reporting it.",
    )
    return parser.parse_args(argv)


def _find_iflw(target: Path) -> Path:
    """Accept either the `.iflw` itself or the artifact directory."""
    if target.is_file():
        return target
    if target.is_dir():
        found = sorted(target.rglob("*.iflw"))
        if found:
            return found[0]
        raise DeriveError(f"no .iflw found under {target}")
    raise DeriveError(f"artifact not found: {target}")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    try:
        iflw_path = _find_iflw(Path(args.artifact))
        result = derive(iflw_path)
    except DeriveError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    print(json.dumps(result, ensure_ascii=False, indent=2))

    if result["unresolved_parameters"]:
        nomes = ", ".join(result["unresolved_parameters"])
        print(
            f"[ERROR] {result['iflow']} leaves {nomes} externalized, so the listener "
            "path cannot be derived from the artifact. Supply the value from the tenant "
            "configuration before calling this flow; a URL built from the placeholder "
            "would not resolve.",
            file=sys.stderr,
        )
        return 2

    if result["has_http_trigger"] is not True:
        print(
            f"[WARNING] {result['iflow']} declares no HTTP trigger "
            f"(component type: {result['component_type'] or 'unknown'}). "
            "The tenant polls or schedules this flow, so there is no webhook to call. "
            "A UAT smoke test by HTTP does not apply.",
            file=sys.stderr,
        )
        if args.fail_if_no_trigger:
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
