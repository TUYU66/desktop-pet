#include "bsp_oled_i2c.h"


//IO方向设置 IO direction setting
#define OLED_SDA_IN()  {GPIOB->CRH&=0XFFFFFF0F;GPIOB->CRH|=(u32)8<<4;}
#define OLED_SDA_OUT() {GPIOB->CRH&=0XFFFFFF0F;GPIOB->CRH|=(u32)3<<4;}

//IO操作函数 IO operation function
#define OLED_IIC_SCL    PBout(8) //SCL
#define OLED_IIC_SDA    PBout(9) //SDA	 
#define OLED_READ_SDA   PBin(9)  //输入SDA  Input SDA

// 等待引脚的时间，可根据芯片时钟修改，只要符合通讯要求即可。 The waiting time for the pin can be modified according to the chip clock as long as it meets the communication requirements.
#define DELAY_FOR_COUNT      10


static void Delay_For_Pin(uint8_t nCount)
{
    uint8_t i = 0;
    for(; nCount != 0; nCount--)
    {
        for (i = 0; i < DELAY_FOR_COUNT; i++); 
    }
}


void OLED_I2C_Init(void)
{	
		OLED_IIC_Init();
		OLED_Init();//oled初始化 OLED initialization
	
		OLED_Draw_Line("oled init success!", 1, true, true);
}


void OLED_IIC_Init(void)
{
    RCC->APB2ENR |= 1 << 3;   //先使能外设IO PORTB时钟 First enable the peripheral IO PORTB clock
    GPIOB->CRH &= 0XFFFFFF00; //PB 8/9 推挽输出 Push-pull output
    GPIOB->CRH |= 0X00000033; 
}

/**
 * @Brief: 产生IIC起始信号 Generate IIC start signal
 * @Note:
 * @Parm: void
 * @Retval: void
 */
int OLED_IIC_Start(void)
{
    OLED_SDA_OUT(); //sda线输出 sda line output
    OLED_IIC_SDA = 1;
    if (!OLED_READ_SDA)
        return 0;
    OLED_IIC_SCL = 1;
    Delay_For_Pin(1);
    OLED_IIC_SDA = 0; //START:when CLK is high,DATA change form high to low
    if (OLED_READ_SDA)
        return 0;
    Delay_For_Pin(2);
    OLED_IIC_SCL = 0; //钳住I2C总线，准备发送或接收数据 Clamp the I2C bus and prepare to send or receive data
    return 1;
}

/**
 * @Brief: 产生IIC停止信号 Generate IIC stop signal
 * @Note:
 * @Parm: void
 * @Retval: void
 */
void OLED_IIC_Stop(void)
{
    OLED_SDA_OUT(); //sda线输出 sda line output
    OLED_IIC_SCL = 0;
    OLED_IIC_SDA = 0; //STOP:when CLK is high DATA change form low to high
    Delay_For_Pin(2);
    OLED_IIC_SCL = 1;
    Delay_For_Pin(1);
    OLED_IIC_SDA = 1; //发送I2C总线结束信号 Send I2C bus end signal
    Delay_For_Pin(2);
}

/**
 * @Brief: 等待应答信号到来 Waiting for the response signal to arrive
 * @Note:
 * @Parm:
 * @Retval: 1:接收应答成功(Successful response reception) | 0:接收应答失败(Failed response reception)
 */
int OLED_IIC_Wait_Ack(void)
{
    uint8_t ucErrTime = 0;
    OLED_SDA_IN(); //SDA设置为输入 SDA is set as input
    OLED_IIC_SDA = 1;
    Delay_For_Pin(1);
    OLED_IIC_SCL = 1;
    Delay_For_Pin(1);
    while (OLED_READ_SDA)
    {
        ucErrTime++;
        if (ucErrTime > 50)
        {
            OLED_IIC_Stop();
            return 0;
        }
        Delay_For_Pin(1);
    }
    OLED_IIC_SCL = 0; //时钟输出0 Clock output 0
    return 1;
}

/**
 * @Brief: 产生ACK应答 Generate ACK response
 * @Note:
 * @Parm: void
 * @Retval: void
 */
void OLED_IIC_Ack(void)
{
    OLED_IIC_SCL = 0;
    OLED_SDA_OUT();
    OLED_IIC_SDA = 0;
    Delay_For_Pin(1);
    OLED_IIC_SCL = 1;
    Delay_For_Pin(1);
    OLED_IIC_SCL = 0;
}

/**
 * @Brief: 产生NACK应答 Generate NACK response
 * @Note:
 * @Parm: void
 * @Retval: void
 */
void OLED_IIC_NAck(void)
{
    OLED_IIC_SCL = 0;
    OLED_SDA_OUT();
    OLED_IIC_SDA = 1;
    Delay_For_Pin(1);
    OLED_IIC_SCL = 1;
    Delay_For_Pin(1);
    OLED_IIC_SCL = 0;
}

/**
 * @Brief: IIC发送一个字节 IIC sends a byte
 * @Note:
 * @Parm: void
 * @Retval: void
 */
void OLED_IIC_Send_Byte(uint8_t txd)
{
    uint8_t t;
    OLED_SDA_OUT();
    OLED_IIC_SCL = 0; //拉低时钟开始数据传输 Pull the clock low to start data transmission
    for (t = 0; t < 8; t++)
    {
        OLED_IIC_SDA = (txd & 0x80) >> 7;
        txd <<= 1;
        Delay_For_Pin(1);
        OLED_IIC_SCL = 1;
        Delay_For_Pin(1);
        OLED_IIC_SCL = 0;
        Delay_For_Pin(1);
    }
}


/**
 * @Brief: I2C写数据函数 I2C write data function
 * @Note:
 * @Parm: addr:I2C地址(I2C address) | reg:寄存器(register) | len:数据长度(data length) | data:数据指针(data pointer)
 * @Retval: 0:停止(Stop) | 1:连续写(Continuous writing)
 */
int OLED_i2cWrite(uint8_t addr, uint8_t reg, uint8_t len, uint8_t *data)
{
    int i;
    if (!OLED_IIC_Start())
        return 1;
    OLED_IIC_Send_Byte(addr << 1);
    if (!OLED_IIC_Wait_Ack())
    {
        OLED_IIC_Stop();
        return 1;
    }
    OLED_IIC_Send_Byte(reg);
    OLED_IIC_Wait_Ack();
    for (i = 0; i < len; i++)
    {
        OLED_IIC_Send_Byte(data[i]);
        if (!OLED_IIC_Wait_Ack())
        {
            OLED_IIC_Stop();
            return 0;
        }
    }
    OLED_IIC_Stop();
    return 0;
}

/** 
 * @Brief: I2C读函数 I2C Read Function
 * @Note:
 * @Parm: 参数同写类似 Parameters are similar to those written
 * @Retval:
 */
int OLED_i2cRead(uint8_t addr, uint8_t reg, uint8_t len, uint8_t *buf)
{
    if (!OLED_IIC_Start())
        return 1;
    OLED_IIC_Send_Byte(addr << 1);
    if (!OLED_IIC_Wait_Ack())
    {
        OLED_IIC_Stop();
        return 1;
    }
    OLED_IIC_Send_Byte(reg);
    OLED_IIC_Wait_Ack();
    OLED_IIC_Start();
    OLED_IIC_Send_Byte((addr << 1) + 1);
    OLED_IIC_Wait_Ack();
    while (len)
    {
        if (len == 1)
            *buf = OLED_IIC_Read_Byte(0);
        else
            *buf = OLED_IIC_Read_Byte(1);
        buf++;
        len--;
    }
    OLED_IIC_Stop();
    return 0;
}

/**
 * @Brief: 读1个字节，ack=1时，发送ACK，ack=0，发送nACK   Read 1 byte, when ack=1, send ACK, when ack=0, send nACK
 * @Note:
 * @Parm:
 * @Retval:
 */
uint8_t OLED_IIC_Read_Byte(unsigned char ack)
{
    unsigned char i, receive = 0;
    OLED_SDA_IN(); //SDA设置为输入 SDA is set as input
    for (i = 0; i < 8; i++)
    {
        OLED_IIC_SCL = 0;
        Delay_For_Pin(2);
        OLED_IIC_SCL = 1;
        receive <<= 1;
        if (OLED_READ_SDA)
            receive++;
        Delay_For_Pin(2);
    }
    if (ack)
        OLED_IIC_Ack(); //发送ACK Send ACK
    else
        OLED_IIC_NAck(); //发送nACK Send nACK
    return receive;
}

/**
 * @Brief: 读取指定设备 指定寄存器的一个值 Read a value of a specified register of a specified device
 * @Note: 
 * @Parm: I2C_Addr  目标设备地址(target device address) | addr     寄存器地址（register address）
 * @Retval: 
 */
unsigned char OLED_I2C_ReadOneByte(unsigned char I2C_Addr, unsigned char addr)
{
    unsigned char res = 0;

    OLED_IIC_Start(); 
    OLED_IIC_Send_Byte(I2C_Addr); //发送写命令 Send write command
    res++;
    OLED_IIC_Wait_Ack();
    OLED_IIC_Send_Byte(addr);
    res++; //发送地址 Shipping Address
    OLED_IIC_Wait_Ack();
	
    OLED_IIC_Start();
    OLED_IIC_Send_Byte(I2C_Addr + 1);
    res++; //进入接收模式 Entering receive mode
    OLED_IIC_Wait_Ack();
    res = OLED_IIC_Read_Byte(0);
    OLED_IIC_Stop(); //产生一个停止条件 Generates a stop condition

    return res;
}

/**
 * @Brief: 读取指定设备 指定寄存器的 length个值 Read length values ​​of the specified register of the specified device
 * @Note: 
 * @Parm: dev  目标设备地址（Target device address） | reg   寄存器地址（Register address） | length 要读的字节数（Number of bytes to read） | *data  读出的数据将要存放的指针（Pointer where the read data will be stored）
 * @Retval: 读出来的字节数量 The number of bytes read
 */
uint8_t OLED_OLED_IICreadBytes(uint8_t dev, uint8_t reg, uint8_t length, uint8_t *data)
{
    uint8_t count = 0;

    OLED_IIC_Start();
    OLED_IIC_Send_Byte(dev); //发送写命令 Send write command
    OLED_IIC_Wait_Ack();
    OLED_IIC_Send_Byte(reg); //发送地址 Shipping Address
    OLED_IIC_Wait_Ack();
    OLED_IIC_Start();
    OLED_IIC_Send_Byte(dev + 1); //进入接收模式 Entering receive mode
    OLED_IIC_Wait_Ack();

    for (count = 0; count < length; count++)
    {

        if (count != length - 1)
            data[count] = OLED_IIC_Read_Byte(1); //带ACK的读数据 Read data with ACK
        else
            data[count] = OLED_IIC_Read_Byte(0); //最后一个字节NACK Last byte NACK
    }
    OLED_IIC_Stop(); //产生一个停止条件 Generates a stop condition
    return count;
}

/**
 * @Brief: 将多个字节写入指定设备 指定寄存器 Write multiple bytes to the specified device and register.
 * @Note: 
 * @Parm: dev  目标设备地址（Target device address） | reg   寄存器地址（Register address） | length 要读的字节数（Number of bytes to read） | *data  将要写的数据的首地址（The first address of the data to be written）
 * @Retval: 返回是否成功 Returns whether it is successful
 */
uint8_t OLED_OLED_IICwriteBytes(uint8_t dev, uint8_t reg, uint8_t length, uint8_t *data)
{

    uint8_t count = 0;
    OLED_IIC_Start();
    OLED_IIC_Send_Byte(dev); //发送写命令 Send write command
    OLED_IIC_Wait_Ack(); 
    OLED_IIC_Send_Byte(reg); //发送地址 Send Address
    OLED_IIC_Wait_Ack();
    for (count = 0; count < length; count++)
    {
        OLED_IIC_Send_Byte(data[count]);
        OLED_IIC_Wait_Ack();
    }
    OLED_IIC_Stop(); //产生一个停止条件 Generates a stop condition

    return 1; //status == 0;
}

/**
 * @Brief: 读取指定设备 指定寄存器的一个值 Read a value of a specified register of a specified device
 * @Note: 
 * @Parm: dev  目标设备地址（Target device address） | reg   寄存器地址（Register address） | *data  读出的数据将要存放的地址（The address where the read data will be stored）
 * @Retval: 1
 */
uint8_t OLED_IICreadByte(uint8_t dev, uint8_t reg, uint8_t *data)
{
    *data = OLED_I2C_ReadOneByte(dev, reg);
    return 1;
}

/** 
 * @Brief: 写入指定设备 指定寄存器一个字节 Write a byte to the specified device and register
 * @Note: 
 * @Parm: dev  目标设备地址（Target device address） | reg   寄存器地址（Register address） | data  将要写入的字节（The bytes to be written）
 * @Retval: 1
 */
unsigned char OLED_IICwriteByte(unsigned char dev, unsigned char reg, unsigned char data)
{
    return OLED_OLED_IICwriteBytes(dev, reg, 1, &data);
}

/**
 * @Brief: 读 修改 写 指定设备 指定寄存器一个字节 中的多个位  Read   Modify   Write   Specified device Specified register   Multiple bits in a byte
 * @Note: 
 * @Parm: dev  目标设备地址（Target device address） | reg   寄存器地址（Register address） | bitStart  目标字节的起始位（The start bit of the target byte） | length   位长度（Bit Length） | data    存放改变目标字节位的值（Store the value of the target byte to be changed）
 * @Retval: 1:成功（Success） | 0:失败（Failure）
 */
uint8_t OLED_OLED_IICwriteBits(uint8_t dev, uint8_t reg, uint8_t bitStart, uint8_t length, uint8_t data)
{

    uint8_t b;
    if (OLED_IICreadByte(dev, reg, &b) != 0)
    {
        uint8_t mask = (0xFF << (bitStart + 1)) | 0xFF >> ((8 - bitStart) + length - 1);
        data <<= (8 - length);
        data >>= (7 - bitStart);
        b &= mask;
        b |= data;
        return OLED_IICwriteByte(dev, reg, b);
    }
    else
    {
        return 0;
    }
}

/**
 * @Brief: 读 修改 写 指定设备 指定寄存器一个字节 中的1个位  Read   Modify   Write   Specified device   Specified register   1 bit in a byte
 * @Note: 
 * @Parm: dev  目标设备地址（Target device address） | reg   寄存器地址（Register address） | bitNum  要修改目标字节的bitNum位（The bitNum bit of the target byte to be modified） | data  为0 时，目标位将被清0 否则将被置位（When it is 0, the target bit will be cleared to 0, otherwise it will be set）
 * @Retval: 1:成功（Success） | 0:失败（Failure）
 */
uint8_t OLED_IICwriteBit(uint8_t dev, uint8_t reg, uint8_t bitNum, uint8_t data)
{
    uint8_t b;
    OLED_IICreadByte(dev, reg, &b);
    b = (data != 0) ? (b | (1 << bitNum)) : (b & ~(1 << bitNum));
    return OLED_IICwriteByte(dev, reg, b);
}

