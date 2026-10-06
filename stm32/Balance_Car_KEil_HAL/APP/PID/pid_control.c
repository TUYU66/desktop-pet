#include "pid_control.h"

static volatile float velocity_filtered;
static volatile float velocity_integral;

void PID_ResetVelocity(void)
{
    uint32_t irq = __get_PRIMASK();
    __disable_irq();
    velocity_filtered = 0;
    velocity_integral = 0;
    __set_PRIMASK(irq);
}

void PID_GetVelocityState(float *filtered, float *integral)
{
    uint32_t irq = __get_PRIMASK();
    __disable_irq();
    *filtered = velocity_filtered;
    *integral = velocity_integral;
    __set_PRIMASK(irq);
}




//下面的pid为了好调,都放到100倍了
//The PID below has been increased to 100 times for easy tuning

//直立环PD控制参数
//Vertical loop PD control parameters
volatile float Balance_Kp =9600;//范围0-288  Range 0-288
volatile float Balance_Kd =80; // Operator stable standing baseline (2026-10-04).

//速度环PI控制参数
//PI control parameters for speed loop
volatile float Velocity_Kp=7000;
volatile float Velocity_Ki=23;

//转向环PD控制参数
//Steering ring PD control parameters
float Turn_Kp=1700; //这个根据自 己的需求调，只是平衡可以不调,和旋转速度有关 This can be adjusted according to one's own needs, but the balance can be left unadjusted, depending on the rotation speed
float Turn_Kd=20;

//前进速度
//Forward speed
float Car_Target_Velocity=30;
//旋转速度
//Rotation speed
float Car_Turn_Amplitude_speed=36;

/**************************************************************************
Function: Absolute value function 
Input   : a：Number to be converted
Output  : unsigned int
函数功能：绝对值函数
入口参数：a：需要计算绝对值的数
返回  值：无符号整型
**************************************************************************/	
int myabs(int a)
{ 		   
	int temp;
	if(a<0)  temp=-a;  
	else temp=a; 
	return temp;
}


/**************************************************************************
Function: Vertical PD control
Input   : Angle:angle；Gyro：angular velocity
Output  : balance：Vertical control PWM
函数功能：直立PD控制		
入口参数：Angle:角度；Gyro：角速度
返回  值：balance：直立控制PWM
**************************************************************************/	
int Balance_PD(float Angle,float Gyro)
{  
   float Angle_bias,Gyro_bias;
	 int balance;
	 Angle_bias=Mid_Angle-Angle;                       				//求出平衡的角度中值 和机械相关 Find the median angle and mechanical correlation for equilibrium
	 Gyro_bias=0-Gyro; 
	 balance=-Balance_Kp/100*Angle_bias-Gyro_bias*Balance_Kd/100; //计算平衡控制的电机PWM  PD控制   kp是P系数 kd是D系数  Calculate the motor PWM PD control for balance control kp is the P coefficient kd is the D coefficient
	
	
	 return balance;
}


/**************************************************************************
Function: Speed PI control
Input   : encoder_left：Left wheel encoder reading；encoder_right：Right wheel encoder reading
Output  : Speed control PWM
函数功能：速度控制PWM		
入口参数：encoder_left：左轮编码器读数；encoder_right：右轮编码器读数
返回  值：速度控制PWM
**************************************************************************/
//修改前进后退速度，请修改Target_Velocity，比如，改成60
// To change the forward and backward speed, please modify Target_Velocity, for example, change it to 60
int Velocity_PI(int encoder_left, int encoder_right)
{
    float movement, encoder_error, velocity;
    /* Neither a stopped motor nor rear-support motion is speed regulation.
     * Clear the filter as well as the integral before computing any output. */
    if (Turn_Off(Angle_Balance, battery) == 1 || chassis_rest_state != 0) {
        PID_ResetVelocity();
        return 0;
    }
    if (g_newcarstate == enRUN)
        movement = Car_Target_Velocity;
    else if (g_newcarstate == enBACK)
        movement = -Car_Target_Velocity;
    else
        movement = Move_X;

    encoder_error = 0 - (encoder_left + encoder_right);
    velocity_filtered *= 0.84f;
    velocity_filtered += encoder_error * 0.16f;
    velocity_integral += velocity_filtered;
    velocity_integral += movement;
    if (velocity_integral > 8000) velocity_integral = 8000;
    if (velocity_integral < -8000) velocity_integral = -8000;
    velocity = -velocity_filtered * Velocity_Kp / 100 - velocity_integral * Velocity_Ki / 100;
    return (int)velocity;
}




/**************************************************************************
Function: Turn control
Input   : Z-axis angular velocity
Output  : Turn control PWM
函数功能：转向控制 
入口参数：Z轴陀螺仪
返回  值：转向控制PWM
**************************************************************************/
volatile float myTurn_Kd = 43;
int Turn_PD(float gyro)
{
	 static float Turn_Target,turn_PWM; 
	 float Kp=Turn_Kp,Kd;			//修改转向速度，请修改Turn_Amplitude即可 To modify the steering speed, please modify Turn_Smplitude
	//===================遥控左右旋转部分 Remote control left and right rotation part=================//
    if (g_newcarstate == enLEFT)
        Turn_Target = -Car_Turn_Amplitude_speed;
    else if (g_newcarstate == enRIGHT)
        Turn_Target = Car_Turn_Amplitude_speed;
    else if (g_newcarstate == enTLEFT)
        Turn_Target = -50;
    else if (g_newcarstate == enTRIGHT)
        Turn_Target = 50;
    else
        Turn_Target = 0;

	//如果是遥控走直线 If it is remote control walking in a straight line
	if(g_newcarstate==enRUN || g_newcarstate==enBACK )
	{
		Kd=Turn_Kd; 
	} 
	else Kd=myTurn_Kd; 
	

  //===================转向PD控制器 Turn to PD controller=================//
	 turn_PWM=Turn_Target*Kp/100+gyro*Kd/100+Move_Z; //结合Z轴陀螺仪进行PD控制  Combining Z-axis gyroscope for PD control
	
	
	 return turn_PWM;								 				 //转向环PWM右转为正，左转为负 Steering ring PWM: Right turn is positive, left turn is negative
}




