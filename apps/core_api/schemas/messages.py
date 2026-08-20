from pydantic import BaseModel, ConfigDict, Field


class IncomingMessage(BaseModel):
    """Mensaje entrante normalizado (independiente del proveedor)."""

    model_config = ConfigDict(extra="forbid")

    external_id: str
    contact_phone: str
    contact_name: str = ""
    channel: str = "whatsapp"
    type: str = "text"
    content: str = ""
    metadata: dict = Field(default_factory=dict)


class PipelineResult(BaseModel):
    status: str  # processed | duplicate | no_agent | queued | ignored
    agent_name: str | None = None
    reply: str | None = None
    conversation_id: int | None = None
