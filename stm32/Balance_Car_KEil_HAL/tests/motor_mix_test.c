#include <assert.h>
#include "../APP/motor_mix.h"

int main(void)
{
    int left, right;

    /* Regression: gyro feedback alone must not start a full-strength spin. */
    Motor_MixCommands(0, 1, 0, 1300, 2600, &left, &right);
    assert(left == 1 && right == -1);
    Motor_MixCommands(1, 1, 0, 1300, 2600, &left, &right);
    assert(left == 1302 && right == 1300);
    Motor_MixCommands(-1, -1, 0, 1300, 2600, &left, &right);
    assert(left == -1302 && right == -1300);

    /* The existing left/right/180-degree drive retains wheel compensation. */
    Motor_MixCommands(0, 180, 1, 1300, 2600, &left, &right);
    assert(left == 1480 && right == -1480);
    Motor_MixCommands(0, -180, 1, 1300, 2600, &left, &right);
    assert(left == -1480 && right == 1480);

    Motor_MixCommands(0, 0, 0, 1300, 2600, &left, &right);
    assert(left == 0 && right == 0);
    Motor_MixCommands(1500, 400, 0, 1300, 2600, &left, &right);
    assert(left == 2600 && right == 2400);
    Motor_MixCommands(-1500, -400, 0, 1300, 2600, &left, &right);
    assert(left == -2600 && right == -2400);
    Motor_MixCommands(1500, 400, 1, 1300, 2600, &left, &right);
    assert(left == 2600 && right == 2400);
    return 0;
}
