#include "bsp_bluetooth.h"
#include "app_runtime.h"


void bluetooth_init(void)
{
    /* CubeMX may already have initialized this UART in main(). LL init
     * requires UE=0, so do not initialize an enabled peripheral twice. */
    if (!LL_USART_IsEnabled(UART5)) MX_UART5_Init();
    LL_USART_DisableIT_RXNE(UART5);
    /* F1 clears stale RX/ORE/FE/NE/PE by reading SR followed by DR. Bytes
     * received during the long sensor startup must not trigger an IRQ storm. */
    (void)UART5->SR;
    (void)LL_USART_ReceiveData8(UART5);
    Init_PID();
    LL_USART_EnableIT_RXNE(UART5); // Start receiving interrupt 启动接收中断

	
}




void UART5_Send_Char(char *s)
{
    AppRuntime_Send(1, s, 0);
}

#define BLE_RX_CAPACITY 256U
static uint8_t rx_bytes[BLE_RX_CAPACITY];
static volatile uint16_t rx_head, rx_tail;
static volatile uint8_t rx_invalid;
void Bluetooth_ClearReceive(void)
{
    rx_tail = rx_head;
    rx_invalid = 0;
}
void Bluetooth_ReceivePoll(void)
{
    unsigned int count = 128;
    char packet[80];
    if (rx_invalid) Bluetooth_DiscardInput();
    while (count-- && rx_tail != rx_head) {
        uint32_t mask = __get_PRIMASK();
        uint32_t epoch;
        int complete;
        __disable_irq();
        epoch = AppRuntime_BluetoothEpoch();
        deal_bluetooth(rx_bytes[rx_tail]);
        rx_tail = (uint16_t)((rx_tail + 1) % BLE_RX_CAPACITY);
        complete = Bluetooth_TakePacket(packet, sizeof(packet));
        __set_PRIMASK(mask);
        if (complete) AppRuntime_PostBluetooth(packet, epoch);
    }
}

void UART5_RX_deal(void)
{
    uint32_t status;
    uint8_t received;
    if (!LL_USART_IsEnabledIT_RXNE(UART5)) return;
    status = UART5->SR;
    if (!(status & (USART_SR_RXNE | USART_SR_ORE | USART_SR_FE | USART_SR_NE | USART_SR_PE))) return;
    received = LL_USART_ReceiveData8(UART5);
    if (status & (USART_SR_ORE | USART_SR_FE | USART_SR_NE | USART_SR_PE)) {
        rx_invalid = 1;
        AppRuntime_InvalidateBluetooth();
        return;
    }
    {
        uint16_t next = (uint16_t)((rx_head + 1) % BLE_RX_CAPACITY);
        if (next == rx_tail) { rx_invalid = 1; AppRuntime_InvalidateBluetooth(); }
        else { rx_bytes[rx_head] = received; rx_head = next; }
    }
}
