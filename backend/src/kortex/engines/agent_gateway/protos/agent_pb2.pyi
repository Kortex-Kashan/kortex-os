from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class AgentMessage(_message.Message):
    __slots__ = ("status", "desktop_result")
    STATUS_FIELD_NUMBER: _ClassVar[int]
    DESKTOP_RESULT_FIELD_NUMBER: _ClassVar[int]
    status: AgentStatus
    desktop_result: DesktopCommandResult
    def __init__(self, status: _Optional[_Union[AgentStatus, _Mapping]] = ..., desktop_result: _Optional[_Union[DesktopCommandResult, _Mapping]] = ...) -> None: ...

class GatewayMessage(_message.Message):
    __slots__ = ("session_ack", "desktop_launch", "desktop_click", "desktop_type", "desktop_read_text")
    SESSION_ACK_FIELD_NUMBER: _ClassVar[int]
    DESKTOP_LAUNCH_FIELD_NUMBER: _ClassVar[int]
    DESKTOP_CLICK_FIELD_NUMBER: _ClassVar[int]
    DESKTOP_TYPE_FIELD_NUMBER: _ClassVar[int]
    DESKTOP_READ_TEXT_FIELD_NUMBER: _ClassVar[int]
    session_ack: SessionAck
    desktop_launch: DesktopLaunchCommand
    desktop_click: DesktopClickCommand
    desktop_type: DesktopTypeCommand
    desktop_read_text: DesktopReadTextCommand
    def __init__(self, session_ack: _Optional[_Union[SessionAck, _Mapping]] = ..., desktop_launch: _Optional[_Union[DesktopLaunchCommand, _Mapping]] = ..., desktop_click: _Optional[_Union[DesktopClickCommand, _Mapping]] = ..., desktop_type: _Optional[_Union[DesktopTypeCommand, _Mapping]] = ..., desktop_read_text: _Optional[_Union[DesktopReadTextCommand, _Mapping]] = ...) -> None: ...

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

class UiElementSelector(_message.Message):
    __slots__ = ("automation_id", "name", "control_type")
    AUTOMATION_ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    CONTROL_TYPE_FIELD_NUMBER: _ClassVar[int]
    automation_id: str
    name: str
    control_type: str
    def __init__(self, automation_id: _Optional[str] = ..., name: _Optional[str] = ..., control_type: _Optional[str] = ...) -> None: ...

class DesktopLaunchCommand(_message.Message):
    __slots__ = ("command_id", "application_id", "arguments", "timeout_seconds")
    COMMAND_ID_FIELD_NUMBER: _ClassVar[int]
    APPLICATION_ID_FIELD_NUMBER: _ClassVar[int]
    ARGUMENTS_FIELD_NUMBER: _ClassVar[int]
    TIMEOUT_SECONDS_FIELD_NUMBER: _ClassVar[int]
    command_id: str
    application_id: str
    arguments: _containers.RepeatedScalarFieldContainer[str]
    timeout_seconds: int
    def __init__(self, command_id: _Optional[str] = ..., application_id: _Optional[str] = ..., arguments: _Optional[_Iterable[str]] = ..., timeout_seconds: _Optional[int] = ...) -> None: ...

class DesktopClickCommand(_message.Message):
    __slots__ = ("command_id", "window_handle", "selector")
    COMMAND_ID_FIELD_NUMBER: _ClassVar[int]
    WINDOW_HANDLE_FIELD_NUMBER: _ClassVar[int]
    SELECTOR_FIELD_NUMBER: _ClassVar[int]
    command_id: str
    window_handle: str
    selector: UiElementSelector
    def __init__(self, command_id: _Optional[str] = ..., window_handle: _Optional[str] = ..., selector: _Optional[_Union[UiElementSelector, _Mapping]] = ...) -> None: ...

class DesktopTypeCommand(_message.Message):
    __slots__ = ("command_id", "window_handle", "selector", "text")
    COMMAND_ID_FIELD_NUMBER: _ClassVar[int]
    WINDOW_HANDLE_FIELD_NUMBER: _ClassVar[int]
    SELECTOR_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    command_id: str
    window_handle: str
    selector: UiElementSelector
    text: str
    def __init__(self, command_id: _Optional[str] = ..., window_handle: _Optional[str] = ..., selector: _Optional[_Union[UiElementSelector, _Mapping]] = ..., text: _Optional[str] = ...) -> None: ...

class DesktopReadTextCommand(_message.Message):
    __slots__ = ("command_id", "window_handle", "selector", "max_length")
    COMMAND_ID_FIELD_NUMBER: _ClassVar[int]
    WINDOW_HANDLE_FIELD_NUMBER: _ClassVar[int]
    SELECTOR_FIELD_NUMBER: _ClassVar[int]
    MAX_LENGTH_FIELD_NUMBER: _ClassVar[int]
    command_id: str
    window_handle: str
    selector: UiElementSelector
    max_length: int
    def __init__(self, command_id: _Optional[str] = ..., window_handle: _Optional[str] = ..., selector: _Optional[_Union[UiElementSelector, _Mapping]] = ..., max_length: _Optional[int] = ...) -> None: ...

class DesktopCommandResult(_message.Message):
    __slots__ = ("command_id", "success", "error_code", "error_message", "window_handle", "process_id", "text", "truncated")
    COMMAND_ID_FIELD_NUMBER: _ClassVar[int]
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    ERROR_CODE_FIELD_NUMBER: _ClassVar[int]
    ERROR_MESSAGE_FIELD_NUMBER: _ClassVar[int]
    WINDOW_HANDLE_FIELD_NUMBER: _ClassVar[int]
    PROCESS_ID_FIELD_NUMBER: _ClassVar[int]
    TEXT_FIELD_NUMBER: _ClassVar[int]
    TRUNCATED_FIELD_NUMBER: _ClassVar[int]
    command_id: str
    success: bool
    error_code: str
    error_message: str
    window_handle: str
    process_id: int
    text: str
    truncated: bool
    def __init__(self, command_id: _Optional[str] = ..., success: bool = ..., error_code: _Optional[str] = ..., error_message: _Optional[str] = ..., window_handle: _Optional[str] = ..., process_id: _Optional[int] = ..., text: _Optional[str] = ..., truncated: bool = ...) -> None: ...
