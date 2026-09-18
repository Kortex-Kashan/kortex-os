from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class AgentMessage(_message.Message):
    __slots__ = ("status",)
    STATUS_FIELD_NUMBER: _ClassVar[int]
    status: AgentStatus
    def __init__(self, status: _Optional[_Union[AgentStatus, _Mapping]] = ...) -> None: ...

class GatewayMessage(_message.Message):
    __slots__ = ("session_ack",)
    SESSION_ACK_FIELD_NUMBER: _ClassVar[int]
    session_ack: SessionAck
    def __init__(self, session_ack: _Optional[_Union[SessionAck, _Mapping]] = ...) -> None: ...

class AgentStatus(_message.Message):
    __slots__ = ("machine_installation_id", "status")
    MACHINE_INSTALLATION_ID_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    machine_installation_id: str
    status: str
    def __init__(self, machine_installation_id: _Optional[str] = ..., status: _Optional[str] = ...) -> None: ...

class SessionAck(_message.Message):
    __slots__ = ("session_id",)
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    def __init__(self, session_id: _Optional[str] = ...) -> None: ...
