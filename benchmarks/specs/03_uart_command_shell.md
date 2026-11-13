# UART command interface

Accept newline-terminated commands on the console UART:

- `led on` / `led off`: set led0 and reply `ok`
- `blink <ms>`: blink led0 with the given half-period in milliseconds (50..2000) and reply `ok`;
  out-of-range values reply `error: range`
- `status`: reply `led=<on|off|blink> period=<ms>`
- anything else: reply `error: unknown command`

Command parsing must be a pure module with unit tests. Print `ready` once the interface accepts input.
