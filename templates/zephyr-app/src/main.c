/*
 * Factory smoke app: announce boot on the console, then blink led0 at 1 Hz
 * (500 ms on / 500 ms off).
 */
#include <zephyr/kernel.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/sys/printk.h>

#define BLINK_HALF_PERIOD_MS 500

static const struct gpio_dt_spec led = GPIO_DT_SPEC_GET(DT_ALIAS(led0), gpios);

int main(void)
{
	printk("factory-smoke: boot\n");

	if (!gpio_is_ready_dt(&led)) {
		printk("factory-smoke: led not ready\n");
		return -ENODEV;
	}
	if (gpio_pin_configure_dt(&led, GPIO_OUTPUT_INACTIVE) < 0) {
		printk("factory-smoke: led configure failed\n");
		return -EIO;
	}

	printk("factory-smoke: blinking\n");
	while (1) {
		gpio_pin_toggle_dt(&led);
		k_msleep(BLINK_HALF_PERIOD_MS);
	}
	return 0;
}
