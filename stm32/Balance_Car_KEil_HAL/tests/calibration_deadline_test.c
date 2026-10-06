/* Pure clock arithmetic regression cases. No HAL, wheel output or hardware. */
#include <assert.h>
#include "../APP/calibration_deadline.h"

int main(void)
{
    /* A packet observed at 300 ms but arriving at 1300 ms must still expire
     * at 1800 ms, not acquire another 1500 ms until 2800 ms. */
    uint32_t start = Calibration_LeaseStart(1300U, 300U);
    assert(start == 300U);
    assert(Calibration_SampleFresh(1799U, 300U));
    assert(!Calibration_SampleFresh(1800U, 300U));
    assert(1800U - start == CALIBRATION_LEASE_MS);

    /* 31-bit tool clock wrap and the STM32's full 32-bit wrap remain fresh
     * across a short interval; future timestamps and old pulses are rejected. */
    assert(Calibration_SampleFresh(0x80000020U, 0x7FFFFFE0U));
    assert(Calibration_SampleFresh(0x00000020U, 0x7FFFFFE0U));
    assert(Calibration_LeaseStart(0x00000020U, 0x7FFFFFE0U) == 0xFFFFFFE0U);
    assert(!Calibration_SampleFresh(500U, 501U));
    assert(!Calibration_SampleFresh(10000U, 8000U));
    assert(Calibration_SampleFresh(1200U, 1200U));
    return 0;
}
