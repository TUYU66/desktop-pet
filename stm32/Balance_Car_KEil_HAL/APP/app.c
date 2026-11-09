#include "app.h"
#include "app_runtime.h"
#include "ChassisLink/chassis_link.h"
void app_user(void)
{
    static uint32_t last_upload;
    AppRuntime_ActionPoll();
    ChassisLink_Poll();
    if ((uint32_t)(HAL_GetTick() - last_upload) >= 10U) {
        last_upload = HAL_GetTick();
        SendAutoUp();
    }
}
