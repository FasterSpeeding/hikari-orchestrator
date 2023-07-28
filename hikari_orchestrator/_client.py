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
import collections.abc
import dataclasses
import datetime
import typing

import grpc.aio  # type: ignore
import hikari
from google.protobuf import timestamp_pb2

from . import _protos
from . import _service  # pyright: ignore[reportPrivateUsage]

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
    """Client for interacting with an orchestrator server instance."""

    __slots__ = ("_attributes", "_ca_cert", "_orchestrator_address", "_remote_shards", "_token_hash", "_tracked_shards")

    def __init__(self, token: str, orchestrator_address: str, /, *, ca_cert: bytes | None = None) -> None:
        """Initialies an orchestrator client.

        Parameters
        ----------
        token
            Discord token for the bot that's being orchestrated.
        orchestrator_address
            Address the orchestrator server is being hosted at.
        ca_cert
            The certificate authority being used by the server for TLS SSL.
        """
        self._attributes: _LiveAttributes | None = None
        self._ca_cert = ca_cert
        self._orchestrator_address = orchestrator_address
        self._remote_shards: dict[int, _RemoteShard] = {}
        self._token_hash = _service.hash_token(token)
        self._tracked_shards: dict[int, _TrackedShard] = {}

    def _get_live(self) -> _LiveAttributes:
        if self._attributes:
            return self._attributes

        raise RuntimeError("Client not running")

    @property
    def remote_shards(self) -> collections.abc.Mapping[int, hikari.api.GatewayShard]:
        """Mapping of shard IDs to shard objects.

        These shard objects can be used to remotely monitor and control shards
        and will only be populated while the client is active.
        """
        return self._remote_shards

    def _call_credentials(self) -> grpc.CallCredentials:
        return grpc.access_token_call_credentials(self._token_hash)

    async def fetch_config(self) -> _protos.Config:
        """Fetch the bot config."""
        return await self._get_live().orchestrator.GetConfig(_protos.Undefined(), credentials=self._call_credentials())

    async def fetch_all_states(self) -> collections.abc.Sequence[_protos.Shard]:
        """Fetch the states of all of the bot's shards."""
        states = await self._get_live().orchestrator.GetAllStates(
            _protos.Undefined(), credentials=self._call_credentials()
        )
        return states.shards

    async def start(self) -> None:
        """Start the client by connecting to the orchestrator."""
        if self._attributes:
            raise RuntimeError("Already running")

        if self._ca_cert:
            channel = grpc.aio.secure_channel(self._orchestrator_address, grpc.ssl_channel_credentials(self._ca_cert))

        else:
            channel = grpc.aio.insecure_channel(self._orchestrator_address)

        self._attributes = _LiveAttributes(channel, _protos.OrchestratorStub(channel))
        # TODO: can this value be cached?
        config = await self.fetch_config()
        for shard_id in range(config.shard_count):
            self._remote_shards[shard_id] = _RemoteShard(
                self, shard_id, hikari.Intents(config.intents), config.shard_count
            )

    async def stop(self) -> None:
        """Stop the orchestrator client."""
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

    async def recommended_shard(self, make_shard: collections.abc.Callable[[_protos.Shard], _ShardT], /) -> _ShardT:
        """Acquire the next shard recommended by the server."""
        live_attrs = self._get_live()
        stream = live_attrs.orchestrator.AcquireNext(credentials=self._call_credentials())

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
        """Update the presence of every shard in this bot.

        This state will be remembered between restarts.

        Parameters
        ----------
        idle_since
            The datetime that the user started being idle. If undefined, this
            will not be changed.
        afk
            If `True`, the user is marked as AFK. If `False`,
            the user is marked as being active. If undefined, this will not be
            changed.
        activity
            The activity to appear to be playing. If undefined, this will not be
            changed.
        status
            The web status to show. If undefined, this will not be changed.
        """
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
        await self._get_live().orchestrator.SendPayload(
            _protos.GatewayPayload(presence_update=update), credentials=self._call_credentials()
        )

    async def update_voice_state(
        self,
        guild: hikari.SnowflakeishOr[hikari.PartialGuild],
        channel: hikari.SnowflakeishOr[hikari.GuildVoiceChannel] | None,
        *,
        self_mute: hikari.UndefinedOr[bool] = hikari.UNDEFINED,
        self_deaf: hikari.UndefinedOr[bool] = hikari.UNDEFINED,
    ) -> None:
        """Update the voice state in a given guild.

        Parameters
        ----------
        guild
            The guild or guild ID to update the voice state for.
        channel
            The channel or channel ID to update the voice state for. If `None`
            then the bot will leave the voice channel that it is in for the
            given guild.
        self_mute
            If specified and `True`, the bot will mute itself in that
            voice channel. If `False`, then it will unmute itself.
        self_deaf
            If specified and `True`, the bot will deafen itself in that
            voice channel. If `False`, then it will undeafen itself.
        """
        state = _protos.VoiceState(
            guild_id=int(guild),
            channel_id=None if channel is None else int(channel),
            self_mute=None if self_mute is hikari.UNDEFINED else self_mute,
            self_deaf=None if self_deaf is hikari.UNDEFINED else self_deaf,
        )
        await self._get_live().orchestrator.SendPayload(
            _protos.GatewayPayload(voice_state=state), credentials=self._call_credentials()
        )

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
        """Request for a guild chunk.

        The received guild chunks will be sent to the shard the guild is in,
        not necessarily the current shard.

        !!! note
            To request the full list of members, leave `query` as `""` (empty
            string) and `limit` as `0`.

        Parameters
        ----------
        guild
            The guild to request chunk for.
        include_presences : hikari.undefined.UndefinedOr[bool]
            If provided, whether to request presences.
        query
            If not `""`, request the members which username starts with the string.
        limit
            Maximum number of members to send matching the query.
        users
            If provided, the users to request for.
        nonce
            If provided, the nonce to be sent with guild chunks.

        Raises
        ------
        ValueError
            When trying to specify `users` with `query`/`limit`, if `limit` is not between
            0 and 100, both inclusive or if `users` length is over 100.
        hikari.errors.MissingIntentError
            When trying to request presences without the `GUILD_MEMBERS` or when trying to
            request the full list of members without `GUILD_PRESENCES`.
        """
        if users is not hikari.UNDEFINED:
            if query or limit:
                raise ValueError("Cannot pass `users` when `query` or `limit` have been passed")

            if len(users) > 100:
                raise ValueError("Cannot request more than 100 users")

        if not 0 <= limit <= 100:
            raise ValueError("`limit` must be inclusively between 0 and 100")

        if nonce and len(bytes(nonce, "utf-8")) > 32:
            raise ValueError("`nonce` cannot be longer than 32 bytes")

        request = _protos.RequestGuildMembers(
            guild_id=int(guild),
            include_presences=None if include_presences is hikari.UNDEFINED else include_presences,
            query=query,
            limit=limit,
            users=None if users is hikari.UNDEFINED else map(int, users),
            nonce=None if nonce is hikari.UNDEFINED else nonce,
        )
        await self._get_live().orchestrator.SendPayload(
            _protos.GatewayPayload(request_guild_members=request), credentials=self._call_credentials()
        )


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


# TODO: handle disconnects properly
