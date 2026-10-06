/* Host regression for the actual phone parser; no UART, motors or IMU.
 * Compile this file alone with a host C compiler, then run the resulting binary. */
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

/* Replace the board header with the minimal hardware boundary. */
#define __APP_BLUETOOTH_H_
typedef uint8_t u8;
typedef enum { enSTOP, enRUN, enBACK, enLEFT, enRIGHT, enTLEFT, enTRIGHT } enCarState;
float Balance_Kp = 9600, Balance_Kd = 56, Velocity_Kp = 6200, Velocity_Ki = 15;
float Turn_Kp = 1700, Turn_Kd = 20;
float Velocity_Left, Velocity_Right, Acceleration_Z, Gyro_Balance, battery = 11.4f;
static u8 control_enabled, calibration_active;
static unsigned int moves;
static enCarState last_move;
uint32_t __get_PRIMASK(void) { return 0; }
void __disable_irq(void) {}
void __set_PRIMASK(uint32_t value) { (void)value; }
u8 Chassis_CalibrationActive(void) { return calibration_active; }
u8 Chassis_BluetoothEnabled(void) { return control_enabled; }
void Chassis_BluetoothMove(enCarState state) { ++moves; last_move = state; }
void delay_ms(int milliseconds) { (void)milliseconds; }
void UART5_Send_Char(char *text) { (void)text; }
void ProtocolGetPID(void);

#include "../BSP/Bluetooth/app_bluetooth.c"

static void packet(const char *text)
{
    Bluetooth_DiscardInput();
    while (*text) deal_bluetooth((uint8_t)*text++);
    assert(newLineReceived);
    ProtocolCpyData();
    Protocol();
}

int main(void)
{
    Init_PID();
    control_enabled = 1;
    packet("$1,0,0,0,0,0,0,0,0,0#");
    assert(moves == 1 && last_move == enRUN);
    packet("$0,2,0,0,0,0,0,0,0,0#");
    assert(moves == 2 && last_move == enTRIGHT);

    /* After revocation, continued phone packets cannot move or change PID. */
    control_enabled = 0;
    packet("$1,0,0,0,1,0,0,AP12,AD3#");
    packet("$0,1,0,0,0,0,0,0,0,0#");
    assert(moves == 2 && Balance_Kp == 9600 && Balance_Kd == 56);

    /* A complete packet and a partial packet are both discarded at closure. */
    deal_bluetooth('$'); deal_bluetooth('1'); deal_bluetooth(',');
    Bluetooth_DiscardInput();
    assert(!newLineReceived && !startBit && num == 0 && ProtocolString[0] == 0);
    deal_bluetooth('0'); deal_bluetooth('#');
    assert(!newLineReceived);
    control_enabled = 1;
    packet("$1,0,0,0,0,0,0,0,0,0#");
    assert(moves == 3 && last_move == enRUN);
    calibration_active = 1;
    packet("$2,0,0,0,0,0,0,0,0,0#");
    assert(moves == 3);
    puts("bluetooth_input_test passed");
    return 0;
}
