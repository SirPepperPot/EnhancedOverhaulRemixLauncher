# Network Behavior

The launcher makes one small HTTPS request at startup to read the public current
mod version from:

```text
https://raw.githubusercontent.com/SirPepperPot/EnhancedOverhaulRemixVersion/main/latest.txt
```

The response is limited to 4 KiB and parsed only for a version number. The
request uses an eight-second timeout. No personal information, installation
paths, hardware information, mod files, game files, logs, telemetry, or
credentials are transmitted.

If a newer version is found, the launcher displays a prompt. It can open the
official Nexus Mods Files page in the user's default browser only after the user
chooses to do so:

```text
https://www.nexusmods.com/fortheking2/mods/29?tab=files
```

The launcher does not use the Nexus API and does not automatically download any
mod or update. Installation always requires the user to select a local `.7z` or
`.zip` file that they downloaded manually.

No other launcher feature communicates over the network.
