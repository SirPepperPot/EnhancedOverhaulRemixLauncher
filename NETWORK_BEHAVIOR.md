# Network Behavior

The launcher makes one HTTPS request at startup to check the latest publicly available version number.

It requests:

https://raw.githubusercontent.com/SirPepperPot/EnhancedOverhaulRemixVersion/main/latest.txt

The response is limited to 4 KiB and parsed only for a version number. The
request uses an eight-second timeout. No personal information, installation
paths, hardware information, mod files, game files, logs, telemetry, or
credentials are transmitted.

If a newer version is found, the launcher displays a prompt. The launcher can open the official Nexus Mods Files page in the user's default web browser, but only after the user explicitly chooses to do so.

```text
https://www.nexusmods.com/fortheking2/mods/29?tab=files
```

The launcher does not use the Nexus API and does not automatically download any
mod or update. Installation always requires the user to select a local `.7z` or
`.zip` file that they downloaded manually.

No other launcher feature communicates over the network.
