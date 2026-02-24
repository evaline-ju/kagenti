# Copyright 2025 IBM Corp.
# Licensed under the Apache License, Version 2.0

"""
Configuration API endpoints.
"""

from fastapi import APIRouter, Depends

from app.core.auth import require_roles, ROLE_VIEWER
from app.core.config import settings
from app.models.responses import DashboardConfigResponse

router = APIRouter(prefix="/config", tags=["config"])


@router.get(
    "/dashboards",
    response_model=DashboardConfigResponse,
    dependencies=[Depends(require_roles(ROLE_VIEWER))],
)
async def get_dashboard_config() -> DashboardConfigResponse:
    """
    Get dashboard URLs for observability tools.

    Returns URLs for Phoenix (traces), Kiali (network), MCP Inspector/Proxy,
    and Keycloak console. URLs are read from environment variables that are
    populated from the kagenti-ui-config ConfigMap.
    """
    domain = settings.domain_name

    return DashboardConfigResponse(
        traces=settings.traces_dashboard_url or f"http://phoenix.{domain}:8080",
        network=settings.network_dashboard_url or f"http://kiali.{domain}:8080",
        mcpInspector=settings.mcp_inspector_url or f"http://mcp-inspector.{domain}:8080",
        mcpProxy=settings.mcp_proxy_full_address or f"http://mcp-proxy.{domain}:8080",
        mcpGatewayUrl=settings.mcp_gateway_url or f"http://mcp.127-0-0-1.sslip.io:8080/mcp",
        mcpGatewayInternalUrl=(
            settings.mcp_gateway_internal_url
            or "http://mcp-gateway-istio.gateway-system.svc.cluster.local:8080/mcp"
        ),
        keycloakConsole=(
            settings.keycloak_console_url
            or f"{settings.effective_keycloak_url}/admin/{settings.effective_keycloak_realm}/console/"
        ),
        domainName=domain,
    )
