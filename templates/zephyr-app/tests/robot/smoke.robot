*** Comments ***
Smoke test for the factory template on STM32F4 Discovery in Renode.
Run with: renode-test --variable ELF:/path/to/zephyr.elf smoke.robot

*** Settings ***
Suite Setup       Setup
Suite Teardown    Teardown
Test Teardown     Test Teardown
Resource          ${RENODEKEYWORDS}

*** Variables ***
${ELF}            ${CURDIR}/../../build/zephyr/zephyr.elf
${PLATFORM}       @/work/factory/renode/stm32f4_disco.repl
${UART}           sysbus.usart2
${LED}            sysbus.gpioPortD.UserLED

*** Keywords ***
Create Machine
    Execute Command           mach create
    Execute Command           machine LoadPlatformDescription ${PLATFORM}
    Execute Command           sysbus LoadELF @${ELF}
    Create Terminal Tester    ${UART}    timeout=5
    Create LED Tester         ${LED}     defaultTimeout=5

*** Test Cases ***
Should Announce Boot On Console
    Create Machine
    Start Emulation
    Wait For Line On Uart     factory-smoke: boot
    Wait For Line On Uart     factory-smoke: blinking

Should Blink LED At 1 Hz
    Create Machine
    Start Emulation
    Wait For Line On Uart     factory-smoke: blinking
    Assert LED Is Blinking    testDuration=4    onDuration=0.5    offDuration=0.5    tolerance=0.1
