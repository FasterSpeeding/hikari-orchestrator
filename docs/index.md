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

#### Distributed Bot

On a larger scale Hikari Orchestrator can also be used to manage shards across
different machines.

For this you'll want to first start up an Orchestrator server using the CLI
entry point (or using [run_server][hikari_orchestrator.run_server]):

```shell
hikari_orchestrator tcp://localhost:6969 --token "Bot.Token"
```

The CLI application has two required arguments:

- The server's host address is the only positional argument. TCP will be used
  if no scheme is included and more information on the supported schemes can be
  found [here](https://github.com/grpc/grpc/blob/master/doc/naming.md).
- `--token`: The Discord bot token for the bot being orchestrated.
  It's recommended that you provide this via its env variable rather than as a
  CLI argument .

And several optional arguments:

- `--intents`: The gateway intents the bot should declare. This defaults to
  `ALL_UNPRIVILEGED` and supports passing either the raw integer flag or a
  `|`-separated list of intent names as defined by [hikari.Intents][hikari.intents.Intents]
  (e.g. `GUILD_MEMBERS|GUILD_MODERATION`).
- `--log-level`: Name of the logging level the server should use.
  Defaults to `"INFO"`.
- `--ca-cert` & `--private-key`: Paths to the unencrypted PEM keys which act as
  the certificate authority and private key for the server to use to SSL
  encrypt TCP connections.

These options and arguments can also be provided as environment variables
(including as part of a `.env` file); to see the relevant env variable names
use `hikari_orchestrator --help`.

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
    unecrypted certificate authority PEM as bytes to
    [Bot.\_\_init\_\_][hikari_orchestrator.Bot.__init__] as `ca_cert=bytes`.
