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
from __future__ import annotations

import asyncio
import dataclasses
import datetime
import typing
from collections import abc as collections

import grpc.aio  # type: ignore
import hikari
from google.protobuf import timestamp_pb2

from . import _protos

if typing.TYPE_CHECKING:
    import google.protobuf.message

    _T = typing.TypeVar("_T")
    _ShardT = typing.TypeVar("_ShardT", bound=hikari.api.GatewayShard)
    _StreamT = grpc.aio.StreamStreamCall[_protos.Shard, _protos.Instruction]


def _now() -> timestamp_pb2.Timestamp:
    timestamp = timestamp_pb2.Timestamp()
    timestamp.FromDatetime(datetime.datetime.now(tz=datetime.timezone.utc))
    return timestamp


@dataclasses.dataclass(slots=True)
class _TrackedShard:
    shard: hikari.api.GatewayShard
    stream: _StreamT
    gateway_url: str
    instructions_task: asyncio.Task[None] | None = None
    status_task: asyncio.Task[None] | None = None

    async def disconnect(self) -> None:
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
            gateway_url=self.gateway_url,
        )
        await self.stream.write(state)

    async def _on_ready(self, event: hikari.ShardReadyEvent) -> None:
        # TODO: can we update this earlier?
        self.gateway_url = event.resume_gateway_url
        await self.update_status()


@dataclasses.dataclass(slots=True)
class _LiveAttributes:
    channel: grpc.aio.Channel
    orchestrator: _protos.OrchestratorStub


# TODO: check this implicitly also works for UndefinedNoneOr fields.
def _maybe_undefined(
    message: google.protobuf.message.Message, field: str, field_value: _T, /
) -> hikari.UndefinedOr[_T]:
    name = message.WhichOneof("field")
    assert isinstance(name, str)
    if name and name.startswith("undefined_"):
        return hikari.UNDEFINED

    return field_value


async def _handle_status(shard: _TrackedShard, /) -> None:
    while True:
        await asyncio.sleep(30)
        await shard.update_status()


class Client:
    __slots__ = ("_attributes", "_remote_shards", "_tracked_shards")

    def __init__(self) -> None:
        self._attributes: _LiveAttributes | None = None
        self._remote_shards: dict[int, _RemoteShard] = {}
        self._tracked_shards: dict[int, _TrackedShard] = dataclasses.field(default_factory=dict)

    def _get_live(self) -> _LiveAttributes:
        if self._attributes:
            return self._attributes

        raise RuntimeError("Client not running")

    @property
    def remote_shards(self) -> collections.Mapping[int, hikari.api.GatewayShard]:
        return self._remote_shards

    async def get_config(self) -> _protos.Config:
        return await self._get_live().orchestrator.GetConfig(_protos.Undefined())

    async def get_all_states(self) -> collections.Sequence[_protos.Shard]:
        return (await self._get_live().orchestrator.GetAllStates(_protos.Undefined())).shards

    # TODO: move both args to `__init__`.
    async def start(self, target: str, /, *, credentials: grpc.ChannelCredentials | None = None) -> None:
        if self._attributes:
            raise RuntimeError("Already running")

        if credentials:
            channel = grpc.aio.secure_channel(target, credentials)

        else:
            channel = grpc.aio.insecure_channel(target)

        self._attributes = _LiveAttributes(channel, _protos.OrchestratorStub(channel))
        config = await self.get_config()
        for shard_id in range(config.shard_count):
            self._remote_shards[shard_id] = _RemoteShard(
                self, shard_id, hikari.Intents(config.intents), config.shard_count
            )

    async def stop(self) -> None:
        if not self._attributes:
            raise RuntimeError("Not running")

        # TODO: track when this is closing to not allow multiple concurrent calls calls
        await asyncio.gather(shard.disconnect() for shard in self._tracked_shards.values())
        self._tracked_shards.clear()
        self._remote_shards.clear()
        await self._attributes.channel.close()
        self._attributes = None

    async def acquire_shard(self, shard: hikari.api.GatewayShard, /) -> None:
        raise NotImplementedError

    async def recommended_shard(self, make_shard: collections.Callable[[_protos.Shard], _ShardT], /) -> _ShardT:
        live_attrs = self._get_live()
        stream = live_attrs.orchestrator.AcquireNext()

        instruction = await anext(aiter(stream))

        if instruction.type is _protos.DISCONNECT:
            raise RuntimeError("Failed to connect")

        if instruction.type is not _protos.InstructionType.CONNECT or instruction.shard_id is None:
            raise NotImplementedError(instruction.type)

        shard = make_shard(instruction.shard_state)
        self._tracked_shards[instruction.shard_id] = tracked_shard = _TrackedShard(
            shard, stream, instruction.shard_state.gateway_url
        )

        try:
            # TODO: handle RuntimeError from failing to start better
            await shard.start()

            tracked_shard.instructions_task = asyncio.create_task(self._handle_instructions(tracked_shard))
            tracked_shard.status_task = asyncio.create_task(_handle_status(tracked_shard))

        except Exception:  # This currently may raise an error which can't be pickled
            import traceback

            traceback.print_exc()
            raise RuntimeError("Can't pickle error") from None

        return shard

    async def _handle_instructions(self, shard: _TrackedShard, /) -> None:
        async for instruction in shard.stream:
            if instruction.type is _protos.InstructionType.DISCONNECT:
                self._tracked_shards.pop(shard.shard.id)
                await shard.disconnect()
                break

            elif instruction.type is not _protos.InstructionType.GATEWAY_PAYLOAD:
                continue  # TODO: log

            match instruction.WhichOneof("payload"):
                case "presence_update":
                    status = instruction.presence_update.status
                    idle_since = _maybe_undefined(
                        instruction.presence_update, "idle_since", instruction.presence_update.idle_timestamp
                    )
                    afk = instruction.presence_update.afk
                    activity = _maybe_undefined(
                        instruction.presence_update, "activity", instruction.presence_update.activity_payload
                    )
                    if activity:
                        activity = hikari.Activity(name=activity.name, url=activity.url, type=activity.type)

                    await shard.shard.update_presence(
                        idle_since=idle_since.ToDatetime() if idle_since else idle_since,
                        afk=hikari.UNDEFINED if afk is None else afk,
                        activity=activity,
                        status=hikari.UNDEFINED if status is None else hikari.Status(status),
                    )

                case "voice_state":
                    self_deaf = instruction.voice_state.self_deaf
                    self_mute = instruction.voice_state.self_mute
                    await shard.shard.update_voice_state(
                        guild=instruction.voice_state.guild_id,
                        channel=instruction.voice_state.channel_id,
                        self_deaf=hikari.UNDEFINED if self_deaf is None else self_deaf,
                        self_mute=hikari.UNDEFINED if self_mute is None else self_mute,
                    )

                case _:
                    pass  # TODO: log

    async def update_presence(
        self,
        *,
        idle_since: hikari.UndefinedNoneOr[datetime.datetime] = hikari.UNDEFINED,
        afk: hikari.UndefinedOr[bool] = hikari.UNDEFINED,
        activity: hikari.UndefinedNoneOr[hikari.Activity] = hikari.UNDEFINED,
        status: hikari.UndefinedOr[hikari.Status] = hikari.UNDEFINED,
    ) -> None:
        idle_timestamp, undefined_idle = _or_undefined(idle_since)
        if idle_timestamp:
            raw_idle_timestamp = idle_timestamp
            idle_timestamp = timestamp_pb2.Timestamp()
            idle_timestamp.FromDatetime(raw_idle_timestamp)

        activity_payload, undefined_activity = _or_undefined(activity)
        if activity_payload:
            activity_payload = _protos.PresenceActivity(
                name=activity_payload.name, url=activity_payload.url, type=activity_payload.type
            )

        update = _protos.PresenceUpdate(
            idle_timestamp=idle_timestamp,
            undefined_idle=undefined_idle,
            afk=None if afk is hikari.UNDEFINED else afk,
            activity_payload=activity_payload,
            undefined_activity=undefined_activity,
            status=None if status is hikari.UNDEFINED else status,
        )
        await self._get_live().orchestrator.SendPayload(_protos.GatewayPayload(presence_update=update))

    async def update_voice_state(
        self,
        guild: hikari.SnowflakeishOr[hikari.PartialGuild],
        channel: hikari.SnowflakeishOr[hikari.GuildVoiceChannel] | None,
        *,
        self_mute: hikari.UndefinedOr[bool] = hikari.UNDEFINED,
        self_deaf: hikari.UndefinedOr[bool] = hikari.UNDEFINED,
    ) -> None:
        state = _protos.VoiceState(
            guild_id=int(guild),
            channel_id=None if channel is None else int(channel),
            self_mute=None if self_mute is hikari.UNDEFINED else self_mute,
            self_deaf=None if self_deaf is hikari.UNDEFINED else self_deaf,
        )
        await self._get_live().orchestrator.SendPayload(_protos.GatewayPayload(voice_state=state))

    async def request_guild_members(
        self,
        guild: hikari.SnowflakeishOr[hikari.PartialGuild],
        *,
        include_presences: hikari.UndefinedOr[bool] = hikari.UNDEFINED,
        query: str = "",
        limit: int = 0,
        users: hikari.UndefinedOr[hikari.SnowflakeishSequence[hikari.User]] = hikari.UNDEFINED,
        nonce: hikari.UndefinedOr[str] = hikari.UNDEFINED,
    ) -> None:
        request = _protos.RequestGuildMembers(
            guild_id=int(guild),
            include_presences=None if include_presences is hikari.UNDEFINED else include_presences,
            query=query,
            limit=limit,
            users=None if users is hikari.UNDEFINED else map(int, users),
            nonce=None if nonce is hikari.UNDEFINED else nonce,
        )
        await self._get_live().orchestrator.SendPayload(_protos.GatewayPayload(request_guild_members=request))


class _RemoteShard(hikari.api.GatewayShard):
    __slots__ = ("_close_event", "_shard_count", "_id", "_intents", "_manager", "_state")

    def __init__(self, manager: Client, shard_id: int, intents: hikari.Intents, shard_count: int, /) -> None:
        self._close_event = asyncio.Event()
        self._shard_count = shard_count
        self._id = shard_id
        self._intents = intents
        self._manager = manager
        self._state: _protos.Shard | None = None

    @property
    def heartbeat_latency(self) -> float:
        return self._state.latency if self._state else float("nan")

    @property
    def id(self) -> int:
        return self._id

    @property
    def intents(self) -> hikari.Intents:
        return self._intents

    @property
    def is_alive(self) -> bool:
        return bool(self._state and self._state.state is not _protos.ShardState.STOPPED)

    @property
    def is_connected(self) -> bool:
        return bool(self._state and self._state.state is _protos.ShardState.STARTED)

    @property
    def shard_count(self) -> int:
        return self._shard_count

    def get_user_id(self) -> hikari.Snowflake:
        raise NotImplementedError

    async def close(self) -> None:
        raise NotImplementedError("Cannot close remove shards")

    async def join(self) -> None:
        if self._state is _protos.ShardState.STOPPED:
            raise hikari.ComponentStateConflictError("Shard isn't running")

        await self._close_event.wait()

    async def start(self) -> None:
        raise NotImplementedError("Cannot start remote shards")

    async def update_presence(
        self,
        *,
        idle_since: hikari.UndefinedNoneOr[datetime.datetime] = hikari.UNDEFINED,
        afk: hikari.UndefinedOr[bool] = hikari.UNDEFINED,
        activity: hikari.UndefinedNoneOr[hikari.Activity] = hikari.UNDEFINED,
        status: hikari.UndefinedOr[hikari.Status] = hikari.UNDEFINED,
    ) -> None:
        await self._manager.update_presence(idle_since=idle_since, afk=afk, activity=activity, status=status)

    async def update_voice_state(
        self,
        guild: hikari.SnowflakeishOr[hikari.PartialGuild],
        channel: hikari.SnowflakeishOr[hikari.GuildVoiceChannel] | None,
        *,
        self_mute: hikari.UndefinedOr[bool] = hikari.UNDEFINED,
        self_deaf: hikari.UndefinedOr[bool] = hikari.UNDEFINED,
    ) -> None:
        await self._manager.update_voice_state(guild, channel, self_mute=self_mute, self_deaf=self_deaf)

    async def request_guild_members(
        self,
        guild: hikari.SnowflakeishOr[hikari.PartialGuild],
        *,
        include_presences: hikari.UndefinedOr[bool] = hikari.UNDEFINED,
        query: str = "",
        limit: int = 0,
        users: hikari.UndefinedOr[hikari.SnowflakeishSequence[hikari.User]] = hikari.UNDEFINED,
        nonce: hikari.UndefinedOr[str] = hikari.UNDEFINED,
    ) -> None:
        await self._manager.request_guild_members(
            guild, include_presences=include_presences, query=query, limit=limit, users=users, nonce=nonce
        )



def _or_undefined(value: hikari.UndefinedOr[_T]) -> tuple[_T | None, _protos.Undefined | None]:
    if value is hikari.UNDEFINED:
        return None, _protos.Undefined()

    return value, None
