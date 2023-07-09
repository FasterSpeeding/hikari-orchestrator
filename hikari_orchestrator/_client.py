# -*- coding: utf-8 -*-
# BSD 3-Clause License
#
# Copyright (c) 2023, Faster Speeding
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# * Redistributions of source code must retain the above copyright notice, this
#   list of conditions and the following disclaimer.
#
# * Redistributions in binary form must reproduce the above copyright notice,
#   this list of conditions and the following disclaimer in the documentation
#   and/or other materials provided with the distribution.
#
# * Neither the name of the copyright holder nor the names of its
#   contributors may be used to endorse or promote products derived from
#   this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
""""""
from __future__ import annotations

import asyncio
import dataclasses
import datetime
from collections import abc as collections

import grpc.aio  # type: ignore
import hikari
from google.protobuf import timestamp_pb2

from . import _protos

_StreamT = grpc.aio.StreamStreamCall[_protos.Shard, _protos.Instruction]


def _now() -> timestamp_pb2.Timestamp:
    timestamp = timestamp_pb2.Timestamp()
    timestamp.FromDatetime(datetime.datetime.now(tz=datetime.timezone.utc))
    return timestamp


@dataclasses.dataclass(slots=True)
class _TrackedShard:
    live_attributes: _LiveAttributes
    shard: hikari.api.GatewayShard
    stream: _StreamT
    instructions_task: asyncio.Task[None] | None = None
    status_task: asyncio.Task[None] | None = None

    async def disconnect(self) -> None:
        self.live_attributes.shards.pop(self.shard.id)
        if self.status_task:
            self.status_task.cancel()

        if self.instructions_task and asyncio.current_task() is not self.instructions_task:
            self.instructions_task.cancel()

        await self.shard.close()
        await self.update_status(status=_protos.ShardState.STOPPED)
        await self.stream.done_writing()

    async def update_status(self, *, status: _protos.ShardState = _protos.ShardState.STARTED) -> None:
        assert isinstance(self.shard, hikari.impl.GatewayShardImpl)
        seq = self.shard._seq  # pyright: ignore[reportPrivateUsage]  # TODO: Export this publicly
        session_id = self.shard._session_id  # pyright: ignore[reportPrivateUsage]  # TODO: Export this publicly
        state = _protos.Shard(
            state=status,
            last_seen=_now(),
            latency=self.shard.heartbeat_latency,
            session_id=session_id,
            seq=seq,
            shard_id=self.shard.id,
        )
        await self.stream.write(state)


@dataclasses.dataclass(slots=True)
class _LiveAttributes:
    channel: grpc.aio.Channel
    orchestrator: _protos.OrchestratorStub
    shards: dict[int, _TrackedShard] = dataclasses.field(default_factory=dict)


async def _handle_instructions(shard: _TrackedShard, /) -> None:
    async for instruction in shard.stream:
        if instruction.type is _protos.InstructionType.DISCONNECT:
            await shard.disconnect()
            break

        else:
            ...  # TODO: log lol


async def _handle_status(shard: _TrackedShard, /) -> None:
    while True:
        await shard.update_status()
        await asyncio.sleep(30)


class Client:
    __slots__ = ("_attributes",)

    def __init__(self) -> None:
        self._attributes: _LiveAttributes | None = None

    def _get_live(self) -> _LiveAttributes:
        if self._attributes:
            return self._attributes

        raise RuntimeError("Client not running")

    async def start(self, target: str, /, *, credentials: grpc.ChannelCredentials | None = None) -> None:
        if self._attributes:
            raise RuntimeError("Already running")

        if credentials:
            channel = grpc.aio.secure_channel(target, credentials)

        else:
            channel = grpc.aio.insecure_channel(target)

        self._attributes = _LiveAttributes(channel, _protos.OrchestratorStub(channel))

    async def stop(self) -> None:
        if not self._attributes:
            raise RuntimeError("Not running")

        attributes = self._attributes
        self._attributes = None
        await asyncio.gather(shard.disconnect() for shard in attributes.shards.values())
        await attributes.channel.close()

    async def acquire_shard(self, shard: hikari.api.GatewayShard, /) -> None:
        live_attrs = self._get_live()
        old_state = await live_attrs.orchestrator.GetState(_protos.ShardId(shard.id))

        if old_state.session_id and old_state.seq:
            ...  # TODO: try to reconnect?

        stream = live_attrs.orchestrator.Acquire()
        state = _protos.Shard(state=_protos.ShardState.STARTING, last_seen=_now())
        live_attrs.shards[shard.id] = tracked_shard = _TrackedShard(live_attrs, shard, stream)
        tracked_shard.instructions_task = asyncio.create_task(_handle_instructions(tracked_shard))
        tracked_shard.status_task = asyncio.create_task(_handle_status(tracked_shard))
        await stream.write(state)

    async def recommend_shard(self, make_shard: collections.Callable[[int], hikari.api.GatewayShard], /) -> None:
        ...

    async def close_shard(self, shard_id: int, /) -> None:
        await self._get_live().shards[shard_id].disconnect()
