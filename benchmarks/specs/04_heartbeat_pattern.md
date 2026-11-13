# Heartbeat LED pattern with uptime log

Blink led0 in a heartbeat pattern repeating every 1000 ms: on 100 ms, off 100 ms, on 100 ms,
off 700 ms. Every 5 seconds print `uptime <seconds>s` on the console, where seconds is the whole
number of seconds since boot. The pattern timing must be driven by a table in a pure module.
