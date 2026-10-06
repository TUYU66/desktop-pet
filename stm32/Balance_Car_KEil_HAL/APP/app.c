#include "app.h"
#include "ChassisLink/chassis_link.h"

extern volatile u8 newLineReceived;
extern u8 bulettohflag;

// The foreground loop services the installed Bluetooth module.
// Balance and battery sampling remain in their interrupt callbacks.
void app_user(void)
{
    ChassisLink_Poll();
    if (newLineReceived)
    {
        ProtocolCpyData();
        Protocol();
    }

    if (bulettohflag)
    {
        bulettohflag = 0;
        SendAutoUp();
    }
}
