#include "app_motor.h"
#include "motor_mix.h"

static MotorCompensationState balance_left_state, balance_right_state;
static const MotorThresholds left_thresholds = {
    MOTOR_LEFT_FORWARD_START_PWM, MOTOR_LEFT_FORWARD_RUN_PWM,
    MOTOR_LEFT_REVERSE_START_PWM, MOTOR_LEFT_REVERSE_RUN_PWM
};
static const MotorThresholds right_thresholds = {
    MOTOR_RIGHT_FORWARD_START_PWM, MOTOR_RIGHT_FORWARD_RUN_PWM,
    MOTOR_RIGHT_REVERSE_START_PWM, MOTOR_RIGHT_REVERSE_RUN_PWM
};

uint8_t angle_max = 40;

float Velocity_Left,Velocity_Right;	//车轮速度(mm/s)  Wheel speed (mm/s)


//过滤死区 Filter dead zone
int PWM_Ignore(int pulse)
{
    return Motor_CompensateDeadzone(pulse, MOTOR_IGNORE_PULSE);
}

void Motor_ResetBalanceCompensation(void)
{
    uint32_t irq = __get_PRIMASK();
    __disable_irq();
    Motor_ResetCompensationState(&balance_left_state);
    Motor_ResetCompensationState(&balance_right_state);
    __set_PRIMASK(irq);
}

void Motor_MixBalance(int common, int yaw, u8 commanded_turn,
                      int encoder_left, int encoder_right, int *left, int *right)
{
    Motor_MixMeasured(common, yaw, commanded_turn, encoder_left, encoder_right, HAL_GetTick(),
                      &left_thresholds, &right_thresholds, &balance_left_state, &balance_right_state,
                      MOTOR_BALANCE_PWM_LIMIT, left, right);
}

unsigned int Motor_GetAssistMask(void)
{
    return (balance_left_state.assist_active ? 1U : 0U) |
           (balance_right_state.assist_active ? 2U : 0U);
}

void Motor_FormatCompensation(char *text, unsigned int size)
{
    /* Four start/run pairs: left forward/reverse, right forward/reverse;
     * then blend, rise/ms, quiet, demand, assist, neutral and cooldown ms. */
    snprintf(text, size, "DIAG,COMP,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%u,%u,%u,%u,%u\n",
             left_thresholds.forward_start, left_thresholds.forward_run,
             left_thresholds.reverse_start, left_thresholds.reverse_run,
             right_thresholds.forward_start, right_thresholds.forward_run,
             right_thresholds.reverse_start, right_thresholds.reverse_run,
             MOTOR_COMP_BLEND_PWM, MOTOR_COMP_RISE_PWM_PER_MS,
             (unsigned int)MOTOR_COMP_QUIET_MS, (unsigned int)MOTOR_COMP_DEMAND_MS,
             (unsigned int)MOTOR_COMP_ASSIST_MS, (unsigned int)MOTOR_COMP_NEUTRAL_MS,
             (unsigned int)MOTOR_COMP_COOLDOWN_MS);
}

/**************************************************************************
Function: Assign to PWM register
Input   : motor_left：Left wheel PWM；motor_right：Right wheel PWM
Output  : none
函数功能：赋值给PWM寄存器
入口参数：左轮PWM、右轮PWM
返回  值：无
**************************************************************************/
void Set_Pwm(int motor_left,int motor_right)
{
	if(motor_left == 0)//停车 stop
	{
		L_PWMA = 0;
		L_PWMB = 0;
	}
	if(motor_right == 0)
	{
		R_PWMA = 0;
		R_PWMB = 0;
	}
	
	//左轮  Left wheel
  if(motor_left>0)	 //前进   go ahead
	{
		L_PWMB = myabs(motor_left);
		L_PWMA = 0;
	}		
	else
	{
		L_PWMB = 0;
		L_PWMA = myabs(motor_left);
	}
	
	//右轮 Right wheel
	if(motor_right>0) //前进
	{
		R_PWMA = myabs(motor_right);
		R_PWMB = 0;
	}
	else //后退 Back
	{
		R_PWMA = 0;
		R_PWMB = myabs(motor_right);	
	}

}


/**************************************************************************
Function: PWM limiting range
Input   : IN：Input  max：Maximum value  min：Minimum value
Output  : Output
函数功能：限制PWM赋值 
入口参数：IN：输入参数  max：限幅最大值  min：限幅最小值
返回  值：限幅后的值
**************************************************************************/
int PWM_Limit(int IN,int max,int min)
{
	int OUT = IN;
	if(OUT>max) OUT = max;
	if(OUT<min) OUT = min;
	return OUT;
}


/**************************************************************************
Function: Encoder reading is converted to speed (mm/s)
Input   : none
Output  : none
函数功能：编码器读数转换为速度（mm/s）
入口参数：无
返回  值：无
**************************************************************************/
void Get_Velocity_Form_Encoder(int encoder_left,int encoder_right)
{ 	
	float Rotation_Speed_L,Rotation_Speed_R;						//电机转速  转速=编码器读数（5ms每次）*读取频率/倍频数/减速比/编码器精度 //Motor speed=Encoder reading (5ms each time) * Reading frequency/harmonics/reduction ratio/Encoder accuracy
	Rotation_Speed_L = encoder_left*Control_Frequency/EncoderMultiples/Reduction_Ratio/Encoder_precision;
	Velocity_Left = Rotation_Speed_L*PI*Diameter_67/10;		//求出编码器速度=转速*周长 /10换成cm //Calculate the encoder speed=rotational speed * circumference/10 and convert it to cm
	Rotation_Speed_R = encoder_right*Control_Frequency/EncoderMultiples/Reduction_Ratio/Encoder_precision;
	Velocity_Right = Rotation_Speed_R*PI*Diameter_67/10;		//求出编码器速度=转速*周长 /10换成cm //Calculate the encoder speed=rotational speed * circumference/10 and convert it to cm
	
}



/**************************************************************************
Function: If abnormal, turn off the motor
Input   : angle：Car inclination；voltage：Voltage
Output  : 1：abnormal；0：normal
函数功能：异常关闭电机		
入口参数：angle：小车倾角；voltage：电压
返回  值：1：异常  0：正常
**************************************************************************/	
uint8_t Turn_Off(float angle, float voltage)
{
	u8 temp;
	if(angle<-40||angle>angle_max || voltage<9.6 || Stop_Flag==1)//电池电压低于10V关闭电机 || battery<9.6V   The battery voltage is lower than 10V and the motor is turned off || battery<9.6V
	{	                                                 //倾角大于40度关闭电机 //Turn off the motor if the inclination angle is greater than 40 degrees
		temp=1;                                          
		L_PWMA = 0;
		L_PWMB = 0;
		R_PWMA = 0;
		R_PWMB = 0;
	}
	else
		temp=0;
	return temp;			
}




