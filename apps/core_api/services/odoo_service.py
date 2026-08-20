"""Adapter de Odoo — Fase 3 del roadmap. Solo el contrato queda definido en
providers.ERPProvider; la implementación (XML-RPC/JSON-RPC contra sale.order,
res.partner, stock.quant, con idempotency_key como client_order_ref y
autorización por partner resuelto server-side) se construye en Fase 3."""

from apps.core_api.services.providers import ERPProvider


class OdooProvider(ERPProvider):
    def __init__(self, url: str, db: str, user: str, password: str) -> None:
        raise NotImplementedError("Integración Odoo: Fase 3 del roadmap")

    async def get_partner_by_phone(self, phone: str) -> dict | None:
        raise NotImplementedError

    async def create_order(self, partner_id: int, lines: list[dict], idempotency_key: str) -> dict:
        raise NotImplementedError
