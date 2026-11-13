# Pedestrian crossing state machine

Implement a pedestrian crossing controller as a state machine, printing each state transition on
the console as `state: <NAME>`.

States: GREEN (cars go) -> on button (sw0) press -> YELLOW for 2 s -> RED for 4 s -> GREEN.
Button presses while not in GREEN are ignored but print `request ignored`. While RED, led0 is on;
in all other states led0 is off. Start in GREEN and print `state: GREEN` at boot.

The state machine logic (transitions and timings) must be a pure module with unit tests.
