<!-- # Home -->
# Hikari Orchestrator

A cute lil tool for orchestrating separate Hikari shard clusters.

### Usage

#### Subprocess Bot

Hikari Orchestrator can be used to shard a bot across multiple local threads on
a single system. This uses subprocesses to allow the child bots to run in
parallel.

```py
--8<-- "./docs_src/index.py:20:23"
```

Here we provide the bot's token and a callback which'll be called to setup each
subprocesses' separate bot instance.

`intents` can also be passed to [run_subprocesses][hikari_orchestrator.run_subprocesses]
to specify the intended gateway intents.

!!! warning
    Since the callback is passed to child processes it needs to be
    [picklable](https://docs.python.org/3/library/pickle.html#what-can-be-pickled-and-unpickled).

```shell
hikari_orchestrator run --entrypoint module.path:func --token "Bot.Token"
```

Subprocess bots can also be created using the `hikari_orchestrator run`
(`hor run` for short) CLI command.

This CLI command has two required arguments:

- `--entrypoint`/`-ep` (`ORCHESTRATOR_ENTRYPOINT`): Path to the function which
  will be called with each initialised
  [hikari.GatewayBotAware][hikari.traits.GatewayBotAware] object.
  This must be in the format of  `{module_path}:{function_name}`.
- `token` (`DISCORD_TOKEN`): The bot's Discord token. It's recommended that
  you provide this via its env variable rather than as a CLI argument.

And several optional arguments:

- `--intents` (`ORCHESTRATOR_INTENTS`): The gateway intents the bot should
  declare. This defaults to `ALL_UNPRIVILEGED` and supports passing either the
  raw integer flag or a `|`-separated list of intent names as defined by
  [hikari.Intents][hikari.intents.Intents] (e.g. `"GUILD_MEMBERS|GUILD_MODERATION"`).
- `--shard-count` (`ORCHESTRATOR_SHARD_COUNT`): The amount of shards the bot's
  going to havev. Defaults to Discord's recommended amount.
- `--log-level` (`LOG_LEVEL`): Name of the logging level the bot should use.
  Defaults to `"INFO"`.
- `--process-count` (`ORCHESTRATOR_PROCESS_COUNT`): The amount of subprocesses
  to spread the bot over. Default's to the system's CPU thread count.
- `--entrypoint-dir` (`ORCHESTRATOR_ENTRYPOINT_DIR`): The folder to look for the
  entrypoint's module in by adding it to PYTHONPATH. Defaults to the current
  working directory.

These arguments can also be provided using the environment variables which are
shown in brackets (including as part of a `.env` file).

#### Distributed Bot

On a larger scale Hikari Orchestrator can also be used to manage shards across
different machines.

For this you'll want to first start up an Orchestrator server using the
`hikari_orchestrator server` (`hor server` for short) CLI command or using
[run_server][hikari_orchestrator.run_server]:

```shell
hikari_orchestrator server tcp://localhost:6969 --token "Bot.Token"
```

This CLI command has two required arguments:

- (`ORCHESTRATOR_ADDRESS`): The server's host address is the only positional
  argument. TCP will be used if no scheme is included and more information on
  the supported schemes can be found
  [here](https://github.com/grpc/grpc/blob/master/doc/naming.md).
- `--token` (`DISCORD_TOKEN`): The Discord bot token for the bot being
  orchestrated. It's recommended that you provide this via its env variable
  rather than as a CLI argument.

And several optional arguments:

- `--intents` (`ORCHESTRATOR_INTENTS`): The gateway intents the bot should
  declare. This defaults to `ALL_UNPRIVILEGED` and supports passing either the
  raw integer flag or a `|`-separated list of intent names as defined by
  [hikari.Intents][hikari.intents.Intents] (e.g. `"GUILD_MEMBERS|GUILD_MODERATION"`).
- `--shard-count` (`ORCHESTRATOR_SHARD_COUNT`): The amount of shards the
  bot's going to have. Defaults to Discord's recommended amount.
- `--log-level` (`LOG_LEVEL`): Name of the logging level the server should use.
  Defaults to `"INFO"`.
- `--ca-cert` & `--private-key` (`ORCHESTRATOR_CA_CERT` & `ORCHESTRATOR_PRIVATE_KEY`):
  Paths to the unencrypted PEM keys which act as the certificate authority and
  private key for the server to use to SSL encrypt TCP connections.

These arguments can also be provided using the environment variables which are
shown in brackets (including as part of a `.env` file).

```py
--8<-- "./docs_src/index.py:27:31"
```

Then you need to startup some child bot instances. For this you'll use
Orchestrator's [Bot][hikari_orchestrator.Bot] implementation of
[GatewayBotAware][hikari.traits.GatewayBotAware] which needs to be given the
Orchestrator server's address and the bot's token but otherwise can be used
just like the standard gateway bot. `local_shard_count` indicates how many
shards the bot instance should try to startup.

!!! note
    To use SSL encryption for TCP connections you'll need to pass the
    unencrypted certificate authority PEM as bytes to
    [Bot.\_\_init\_\_][hikari_orchestrator.Bot.__init__] as `ca_cert=bytes`.
