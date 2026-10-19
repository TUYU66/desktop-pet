#include <assert.h>
#include <stdio.h>
#include "../BSP/ChassisLink/bluetooth_app_session.h"

int main(void)
{
    BluetoothAppSession session = {0};
    assert(!BluetoothApp_Begin(&session, 0, 1, 100));
    assert(BluetoothApp_Begin(&session, 1, 1, 100));
    assert(BluetoothApp_CanExecute(&session, 1, 1));
    assert(!BluetoothApp_Begin(&session, 1, 0, 200)); /* duplicate cannot change desired mode */
    assert(BluetoothApp_Forward(&session, 100));
    assert(!BluetoothApp_Forward(&session, 599));
    assert(BluetoothApp_Forward(&session, 600));
    BluetoothApp_Observe(&session, 1000);
    assert(!BluetoothApp_Expired(&session, 4499));
    assert(BluetoothApp_Expired(&session, 4500));
    assert(BluetoothApp_Begin(&session, 2, 0, 4600));
    assert(!BluetoothApp_CanExecute(&session, 1, 1)); /* cancelled ON cannot run after OFF */
    assert(!BluetoothApp_Complete(&session, 1, 1, 1, 0));
    assert(session.result == 1); /* old result cannot finish the new OFF */
    assert(!BluetoothApp_Expired(&session, 9000)); /* explicit OFF may finish after phone closes */
    BluetoothApp_Complete(&session, 2, 1, 0, 1);
    assert(session.result == 2 && !BluetoothApp_CanExecute(&session, 2, 0));
    assert(BluetoothApp_Begin(&session, 3, 1, UINT32_MAX - 1000U));
    assert(!BluetoothApp_Expired(&session, 2000)); /* monotonic tick wraps */
    assert(BluetoothApp_Expired(&session, 3000));
    BluetoothApp_Complete(&session, 3, 1, 0, 0);
    assert(session.result == 3); /* ACK without actual permission is not completion */
    assert(!BluetoothApp_Complete(&session, 3, 1, 1, 0)); /* duplicate cannot trigger cleanup again */
    assert(BluetoothApp_Begin(&session, 4, 0, 100));
    assert(BluetoothApp_Expired(&session, 35100));
    puts("bluetooth_app_session_test passed");
    return 0;
}
