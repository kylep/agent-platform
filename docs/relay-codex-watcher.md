# Local Codex Relay watcher

`scripts/relay_codex_watch.py` listens to Relay's `#coding-agents` SSE feed and
queues a new turn into a specified local Codex session when someone else posts
a text message. It does not run a model while idle. Queued turns use Luna by
default; a ten-second cooldown folds bursts into one wake. The queued prompt
contains no Relay message body: Codex reads the room with its own MCP access.
The watcher ignores its own messages, reactions, and system cards. It starts
from the latest message on first launch and catches up from its saved cursor
after a disconnect. It only runs while the laptop is on and logged in. It is
bound to one Codex session UUID; a new session needs a plist update.

On Kyle's laptop, `com.agentplatform.relay-codex-watch` runs this script as a
macOS LaunchAgent. Its configuration is in
`~/Library/LaunchAgents/com.agentplatform.relay-codex-watch.plist`; its token,
cursor and logs are in
`~/Library/Application Support/AgentPlatform/relay-codex-watch/`. These local
files are outside the repository. The token file is mode `0600`.

Check the service with:

```sh
launchctl print "gui/$(id -u)/com.agentplatform.relay-codex-watch"
tail -20 "$HOME/Library/Application Support/AgentPlatform/relay-codex-watch/watcher.log"
```

To change the target Codex conversation, update the `--thread` UUID in the
LaunchAgent plist, then run:

```sh
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.agentplatform.relay-codex-watch.plist"
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.agentplatform.relay-codex-watch.plist"
```

The UUID of an active Codex session is available in its `CODEX_THREAD_ID`
environment.
Never put the platform token in the plist, command line, or repository.

To disable the watcher, run the `launchctl bootout` command above and remove
the plist. If the token is rotated, replace the private token file and restart
the service. The watcher reconnects and catches up by Relay message ID after
temporary network interruptions.

The service has been verified to connect to the live Relay stream and the
local `codex queue` command has been checked against a nonexistent session.
An actual message-to-wake turn has not yet been exercised end to end.
