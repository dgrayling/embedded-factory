# Pause blinking with the user button

Change the blinky firmware so that each press of the user button (sw0) pauses or resumes the
blinking. When paused, the LED stays off and the firmware prints `paused`; when resumed it prints
`resumed` and blinking continues at the existing rate. Presses within 50 ms of the previous
accepted press are ignored (debounce). Existing console output and blink timing must not change
while running.
