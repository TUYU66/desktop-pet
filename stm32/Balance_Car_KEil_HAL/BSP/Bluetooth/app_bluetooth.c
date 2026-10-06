#include "app_bluetooth.h"
#include <stdlib.h>

#define 	run_car     '1'//按键前 Before pressing the button
#define 	back_car    '2'//按键后 After pressing the button
#define 	left_car    '3'//按键左 Left button
#define 	right_car   '4'//按键右 Right button
#define 	stop_car    '0'//按键停 Press the button to stop

//上报数据 Reporting data
int g_autoup = 0;
char manydisplay[80] ={0};
char updata[80] ={0};
char lspeed[10],rspeed[10],daccel[10],dgyro[10],csb[10],vi[10];

volatile u8 newLineReceived = 0;
int num = 0;
u8 startBit = 0;
int int9num =0;
u8 inputString[80] = {0};
u8 ProtocolString[80] = {0};

enCarState g_newcarstate = enSTOP; //  1前2后3左4右0停止 1 forward 2 backward 3 left 4 right 0 stop


//PID部分  PID section
extern volatile float Balance_Kp,Balance_Kd,Velocity_Kp,Velocity_Ki;
extern float Turn_Kp,Turn_Kd; //引入立直环和速度环,转向环 //Introducing vertical rings, speed rings, and steering rings
char piddisplay[50] ="$AP";
char charkp[10],charkd[10],charksp[10],charksi[10] ,charktp[10],charktd[10];
float PID_Original[6] = {0};




//函数功能：保留6个PID的初始值  Function Function: Retain the initial values of 6 PIDs
void Init_PID(void)
{
	PID_Original [0] = Balance_Kp;
	PID_Original [1] = Balance_Kd;
	PID_Original [2] = Velocity_Kp;
	PID_Original [3] = Velocity_Ki;
	PID_Original [4] = Turn_Kp;
	PID_Original [5] = Turn_Kd;
}

//函数功能:恢复开机的PID值  Function function: Restore the PID value when turned on
void ResetPID(void)
{	
	if(Balance_Kp != PID_Original[0])
	{
		Balance_Kp = PID_Original[0];
	}
	if(Balance_Kd != PID_Original[1])
	{
		Balance_Kd = PID_Original[1];
	}
	if(Velocity_Kp != PID_Original[2])
	{
		Velocity_Kp = PID_Original[2];
	}
	if(Velocity_Ki != PID_Original[3])
	{
		Velocity_Ki = PID_Original[3];
	}
	if(Turn_Kp != PID_Original[4])
	{
		Turn_Kp = PID_Original[4];
	}
	if(Turn_Kd != PID_Original[5])
	{
		Turn_Kd = PID_Original[5];
	}
}	


/* ISR publishes one complete bounded packet; foreground consumes it atomically. */
void deal_bluetooth(uint8_t value)
{
    if (newLineReceived) return;
    if (value == '$') { startBit = 1; num = 0; }
    if (!startBit) return;
    if (num >= (int)sizeof(inputString) - 1) { startBit = 0; num = 0; return; }
    inputString[num++] = value;
    if (value == '#') {
        inputString[num] = 0;
        startBit = 0;
        newLineReceived = 1;
    }
}

void ProtocolCpyData(void)
{
    uint32_t irq = __get_PRIMASK();
    __disable_irq();
    memset(ProtocolString, 0, sizeof(ProtocolString));
    if (newLineReceived) memcpy(ProtocolString, inputString, (size_t)num);
    int9num = num - 1;
    num = 0;
    startBit = 0;
    newLineReceived = 0;
    __set_PRIMASK(irq);
}

void Bluetooth_DiscardInput(void)
{
    uint32_t irq = __get_PRIMASK();
    __disable_irq();
    newLineReceived = 0;
    startBit = 0;
    num = 0;
    inputString[0] = 0;
    ProtocolString[0] = 0;
    __set_PRIMASK(irq);
}

/* Read exactly one finite nonnegative numeric field without copying a packet
 * tail into the original 8/25-byte temporary buffers. */
static int pid_field(const char *name, float *out)
{
    char *end;
    const char *at = strstr((const char *)ProtocolString, name);
    double value;
    if (!at || at == (const char *)ProtocolString || at[-1] != ',') return 0;
    at += 2;
    value = strtod(at, &end);
    if (end == at || (*end != ',' && *end != '#') || !(value >= 0 && value <= 10000)) return 0;
    *out = (float)value;
    return 1;
}

void Protocol(void)
{
    if (Chassis_CalibrationActive()) return;
    size_t length = strlen((const char *)ProtocolString);
    unsigned int i;
    enCarState requested = enSTOP;
    float kp, kd;
    /* Validate the fixed command header BEFORE changing any motor state. */
    if (length < 21 || ProtocolString[0] != '$' || ProtocolString[length-1] != '#') return;
    for (i = 2; i <= 12; i += 2) if (ProtocolString[i] != ',') return;
    if (ProtocolString[1] < '0' || ProtocolString[1] > '4' ||
        ProtocolString[3] < '0' || ProtocolString[3] > '2') return;
    if (Chassis_BluetoothEnabled()) {
        switch (ProtocolString[1]) {
            case run_car: requested = enRUN; break;
            case back_car: requested = enBACK; break;
            case left_car: requested = enLEFT; break;
            case right_car: requested = enRIGHT; break;
            default: break;
        }
        if (ProtocolString[3] == '1') requested = enTLEFT;
        if (ProtocolString[3] == '2') requested = enTRIGHT;
        Chassis_BluetoothMove(requested);
    }
    if (ProtocolString[5] == '1') {
        ProtocolGetPID();
        delay_ms(5);
        ProtocolGetPID();
    } else if (ProtocolString[5] == '2' && Chassis_BluetoothEnabled()) {
        ResetPID();
        ProtocolGetPID();
        UART5_Send_Char("$OK#");
    }
    if (ProtocolString[7] == '1') { g_autoup = 1; UART5_Send_Char("$OK#"); }
    else if (ProtocolString[7] == '2') { g_autoup = 0; UART5_Send_Char("$OK#"); }
    /* Queries remain available when disabled; writes require explicit control. */
    if (!Chassis_BluetoothEnabled()) return;
    if (ProtocolString[9] == '1' && pid_field("AP", &kp) && pid_field("AD", &kd)) {
        Balance_Kp = kp * 100; Balance_Kd = kd; UART5_Send_Char("$OK#");
    }
    if (ProtocolString[11] == '1' && pid_field("VP", &kp) && pid_field("VI", &kd)) {
        Velocity_Kp = kp * 100; Velocity_Ki = kd; UART5_Send_Char("$OK#");
    }
    if (ProtocolString[13] == '1' && pid_field("TP", &kp) && pid_field("TD", &kd)) {
        Turn_Kp = kp * 100; Turn_Kd = kd; UART5_Send_Char("$OK#");
    }
}


float s_Acc = 0, s_Gyro = 0;
void CalcUpData(void)
{
	float ls, rs,sLence;
	
	if(g_autoup == 1)
	{
		ls = Velocity_Left;//左电机速度  left speed
		rs = Velocity_Right;//右电机速度  right speed
		s_Acc = Acceleration_Z/100; //加速度  acceleration //为了能在app显示完全，缩小100倍  In order to fully display it in the app, it is reduced by 100 times
		s_Gyro = Gyro_Balance; //陀螺仪  gyroscope
		sLence = 0.0f; // No ultrasonic module is installed; retain the app packet field.
		
	
//		printf("ls:%.2f\t,rs:%.2f,acc:%.2f,gryo:%.2f,dis:%.2f \r\n",ls,rs,s_Acc,s_Gyro,sLence);
		memset(manydisplay, 0x00, 80);
		memcpy(manydisplay, "$LV", 4);
	
		memset(lspeed, 0x00, sizeof(lspeed));
		memset(rspeed, 0x00, sizeof(rspeed));
		memset(daccel, 0x00, sizeof(daccel));
		memset(dgyro, 0x00, sizeof(dgyro));
		memset(csb, 0x00, sizeof(csb));
		memset(vi, 0x00, sizeof(vi));
	
		//左边速度  left speed
		if((ls <= 1000) && (ls >= -1000))
			sprintf(lspeed,"%3.2f",ls);
		else
		{
			return;
		}
			
		//右边速度 right speed
		if((rs <= 1000) && (rs >= -1000))
			sprintf(rspeed,"%3.2f",rs);
		else
		{	
			return;
		}
		
		//角速度  acc
		if((s_Acc > -2000) && (s_Acc < 2000))
			sprintf(daccel,"%3.2f",s_Acc);
		else
		{
			return;
		}
		
		//陀螺仪 gryo
		if((s_Gyro > -10000) && (s_Gyro < 10000))
			sprintf(dgyro,"%3.2f",s_Gyro);
		else
		{
			return;
		}
	
		//超时波距离  ultrasonic
		if((sLence >= 0) && (sLence < 10000))
			sprintf(csb,"%3.2f",(float)sLence);
		else
		{
			return;
		}
		
		//电量  quantity of electricity
		if((battery >= 0) && (battery < 20))
			sprintf(vi,"%3.2f",battery);
		else
		{
			return;
		}
	
		strcat(manydisplay,lspeed);
		strcat(manydisplay,",RV");
		strcat(manydisplay,rspeed);
		strcat(manydisplay,",AC");
		strcat(manydisplay,daccel);
		strcat(manydisplay,",GY");
		strcat(manydisplay,dgyro);
		strcat(manydisplay,",CSB");
		strcat(manydisplay,csb);
		strcat(manydisplay,",VT");
		strcat(manydisplay,vi);
		strcat(manydisplay,"#");
		memset(updata, 0x00, 80);
		memcpy(updata, manydisplay, 80);
	}
	
}


//自动上报  Automatic reporting
int g_uptimes = 1; //自动上报 2秒报一次 Automatically report every 2 seconds
void SendAutoUp(void)
{
	g_uptimes --;
	if ((g_autoup == 1) && (g_uptimes == 0))
	{
		CalcUpData();
		UART5_Send_Char(updata); //返回协议数据包	Return protocol data packet
	}
	if(g_uptimes == 0)
		 g_uptimes = 1;

}


//查讯PID Query PID
void ProtocolGetPID(void)
{
	memset(piddisplay, 0x00, sizeof(piddisplay));
	memcpy(piddisplay, "$AP", 4);

	if(Balance_Kp >= 0 ) //&& Balance_Kp <= 28800  
	{
		sprintf(charkp,"%3.2f",Balance_Kp/100);
	}
	else
	{	
		UART5_Send_Char("$GetPIDError#"); //返回协议数据包  Return protocol data packet
		return;
	}

	
	if(Balance_Kd >= 0 ) //&& Balance_Kd <= 100
	{
		sprintf(charkd,"%3.2f",Balance_Kd);//此值原生传过去 This value is passed natively
	}
	else
	{	
		UART5_Send_Char("$GetPIDError#"); //返回协议数据包  Return protocol data packet
		return;
	}
	
	if(Velocity_Kp >= 0 ) //&& Velocity_Kp <= 20000
	{
		sprintf(charksp,"%3.2f",Velocity_Kp/100);
	}
	else
	{	
		UART5_Send_Char("$GetPIDError#"); //返回协议数据包  Return protocol data packet
		return;
	}

	if(Velocity_Ki >= 0 ) //&& Velocity_Ki <= 100
	{
		sprintf(charksi,"%3.2f",Velocity_Ki); //此值原生传过去  This value is passed natively
	}
	else
	{	
		UART5_Send_Char("$GetPIDError#"); //返回协议数据包 Return protocol data packet
		return;
	}
	
	
	//转向环 TP  Steering ring TP
	if(Turn_Kp >= 0 ) 
	{
		sprintf(charktp,"%3.2f",Turn_Kp/100); 
	}
	else
	{	
		UART5_Send_Char("$GetPIDError#"); //返回协议数据包  Return protocol data packet
		return;
	}
	
	//转向环 TD  Steering ring TD
	if(Turn_Kd >= 0 ) 
	{
		sprintf(charktd,"%3.2f",Turn_Kd); 
	}
	else
	{	
		UART5_Send_Char("$GetPIDError#"); //返回协议数据包  Return protocol data packet
		return;
	}
	
	
	strcat(piddisplay,charkp);
	strcat(piddisplay,",AD");
	strcat(piddisplay,charkd);
	strcat(piddisplay,",VP");
	strcat(piddisplay,charksp);
	strcat(piddisplay,",VI");
	strcat(piddisplay,charksi);
	
	//添加转向环  Add steering ring
	strcat(piddisplay,",TP");
	strcat(piddisplay,charktp);
	strcat(piddisplay,",TD");
	strcat(piddisplay,charktd);
	strcat(piddisplay,"#");
	
	UART5_Send_Char(piddisplay); //返回协议数据包  Return protocol data packet

}



