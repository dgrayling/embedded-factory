# Button toggles LED

The LED (led0) starts off. Each press of the user button (sw0) toggles the LED and prints
`led: on` or `led: off` on the console. Presses must be debounced (ignore edges within 50 ms
of the previous accepted press). Print a boot banner at startup.
