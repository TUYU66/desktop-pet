#ifndef BLUETOOTH_APP_SESSION_H
#define BLUETOOTH_APP_SESSION_H
#include <stdint.h>

/* Hardware-independent request fence. A socket connection is not permission. */
typedef struct {
    unsigned int id;
    uint8_t target, result; /* 0 none, 1 pending, 2 completed, 3 failed */
    uint32_t started, heard, forwarded;
} BluetoothAppSession;

static int BluetoothApp_Begin(BluetoothAppSession *state, unsigned int id, unsigned int target, uint32_t now)
{
    if (!id || id > 65535U || target > 1U || state->id == id) return 0;
    state->id = id; state->target = (uint8_t)target; state->result = 1;
    state->started = state->heard = now; state->forwarded = now - 500U;
    return 1;
}
static void BluetoothApp_Observe(BluetoothAppSession *state, uint32_t now) { state->heard = now; }
static int BluetoothApp_Expired(const BluetoothAppSession *state, uint32_t now)
{
    return state->result == 1 && ((uint32_t)(now - state->started) >= 35000U ||
           (state->target && (uint32_t)(now - state->heard) >= 3500U));
}
static int BluetoothApp_Forward(BluetoothAppSession *state, uint32_t now)
{
    if (state->result != 1 || (uint32_t)(now - state->forwarded) < 500U) return 0;
    state->forwarded = now; return 1;
}
static int BluetoothApp_CanExecute(const BluetoothAppSession *state, unsigned int id, unsigned int target)
{
    return state->result == 1 && state->id == id && state->target == target;
}
static int BluetoothApp_Complete(BluetoothAppSession *state, unsigned int id, int success, int enabled, int stopped)
{
    if (state->id != id || state->result != 1) return 0;
    state->result = success && enabled == state->target && (!state->target || !stopped) ? 2 : 3;
    return 1;
}
#endif
