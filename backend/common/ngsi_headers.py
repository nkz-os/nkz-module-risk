"""Shim mínimo de `inject_fiware_headers` para el módulo (no importa core).

Espejo del comportamiento esencial de `services/common/ngsi_headers.py`:
- normaliza el tenant y setea NGSILD-Tenant / Fiware-Service / Fiware-ServicePath.
- @context en body  -> Content-Type application/ld+json, sin Link.
- sin @context       -> Content-Type application/json + Link con CONTEXT_URL.

Regla ETSI NGSI-LD: nunca ld+json y Link a la vez.
"""
import os
import re
from typing import Dict, Optional


def _normalize_tenant(tenant: str) -> str:
    n = tenant.lower().strip().replace(" ", "-")
    n = re.sub(r"[^a-z0-9-]", "", n)
    return n.strip("-") or tenant


def inject_fiware_headers(
    headers: Dict[str, str],
    tenant: Optional[str] = None,
    has_context_in_body: bool = False,
    body: object = None,
) -> Dict[str, str]:
    if body is not None:
        has_context_in_body = isinstance(body, dict) and "@context" in body

    if tenant:
        t = _normalize_tenant(tenant)
        headers["NGSILD-Tenant"] = t
        headers["Fiware-Service"] = t
        headers["Fiware-ServicePath"] = "/"

    context_url = os.getenv("CONTEXT_URL", "")
    if has_context_in_body:
        headers["Content-Type"] = "application/ld+json"
    else:
        headers["Content-Type"] = "application/json"
        if context_url:
            headers["Link"] = (
                f"<{context_url}>; "
                f'rel="http://www.w3.org/ns/json-ld#context"; '
                f'type="application/ld+json"'
            )
    headers.setdefault("Accept", "application/ld+json")
    return headers
