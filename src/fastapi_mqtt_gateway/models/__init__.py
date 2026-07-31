from pydantic import BaseModel, Field


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class TokenData(BaseModel):
    username: str | None = None
    scopes: list[str] = []


class PublishRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=65535)
    payload: str = Field(..., max_length=1048576)
    qos: int = Field(default=0, ge=0, le=2)
    retain: bool = False


class PublishResponse(BaseModel):
    success: bool
    message_id: int | None = None
    topic: str


class SubscribeRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=65535)
    qos: int = Field(default=0, ge=0, le=2)


class SubscribeResponse(BaseModel):
    success: bool
    topic: str
    qos: int


class UnsubscribeRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=65535)


class UnsubscribeResponse(BaseModel):
    success: bool
    topic: str


class RetainedQueryRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=65535)


class RetainedMessage(BaseModel):
    topic: str
    payload: str
    qos: int
    retain: bool = True


class RetainedQueryResponse(BaseModel):
    success: bool
    message: RetainedMessage | None = None
    error: str | None = None


class MQTTMessage(BaseModel):
    topic: str
    payload: str
    qos: int
    retain: bool
    timestamp: float


class TopicInfo(BaseModel):
    topic: str
    message_count: int
    last_message: MQTTMessage | None = None


class HealthResponse(BaseModel):
    status: str
    version: str
    mqtt_connected: bool


class ErrorResponse(BaseModel):
    detail: str
    code: str | None = None
