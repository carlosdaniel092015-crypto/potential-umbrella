import pytest
from django.core.management import call_command

from apps.shared.models import Agent

pytestmark = pytest.mark.django_db


def test_seed_crea_agente_y_es_idempotente():
    call_command("seed_fase1")
    call_command("seed_fase1")

    agents = Agent.objects.filter(name="ventas")
    assert agents.count() == 1
    agent = agents.get()
    assert agent.is_active
    assert "Sued Fargesa" in agent.system_prompt
