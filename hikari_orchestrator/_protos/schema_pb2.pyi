from google.protobuf import timestamp_pb2 as _timestamp_pb2
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class InstructionType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = []
    DISCONNECT: _ClassVar[InstructionType]

class ShardState(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = []
    NOT_STARTED: _ClassVar[ShardState]
    STARTING: _ClassVar[ShardState]
    STARTED: _ClassVar[ShardState]
    STOPPED: _ClassVar[ShardState]

class StatusType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = []
    FAILED: _ClassVar[StatusType]
    SUCCESS: _ClassVar[StatusType]
DISCONNECT: InstructionType
NOT_STARTED: ShardState
STARTING: ShardState
STARTED: ShardState
STOPPED: ShardState
FAILED: StatusType
SUCCESS: StatusType

class Instruction(_message.Message):
    __slots__ = ["type"]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    type: InstructionType
    def __init__(self, type: _Optional[_Union[InstructionType, str]] = ...) -> None: ...

class ShardId(_message.Message):
    __slots__ = ["shard_id"]
    SHARD_ID_FIELD_NUMBER: _ClassVar[int]
    shard_id: int
    def __init__(self, shard_id: _Optional[int] = ...) -> None: ...

class Shard(_message.Message):
    __slots__ = ["state", "last_seen", "latency", "session_id", "seq", "shard_id"]
    STATE_FIELD_NUMBER: _ClassVar[int]
    LAST_SEEN_FIELD_NUMBER: _ClassVar[int]
    LATENCY_FIELD_NUMBER: _ClassVar[int]
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    SEQ_FIELD_NUMBER: _ClassVar[int]
    SHARD_ID_FIELD_NUMBER: _ClassVar[int]
    state: ShardState
    last_seen: _timestamp_pb2.Timestamp
    latency: float
    session_id: str
    seq: int
    shard_id: int
    def __init__(self, state: _Optional[_Union[ShardState, str]] = ..., last_seen: _Optional[_Union[_timestamp_pb2.Timestamp, _Mapping]] = ..., latency: _Optional[float] = ..., session_id: _Optional[str] = ..., seq: _Optional[int] = ..., shard_id: _Optional[int] = ...) -> None: ...

class DisconnectResult(_message.Message):
    __slots__ = ["status", "state"]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    status: StatusType
    state: Shard
    def __init__(self, status: _Optional[_Union[StatusType, str]] = ..., state: _Optional[_Union[Shard, _Mapping]] = ...) -> None: ...
