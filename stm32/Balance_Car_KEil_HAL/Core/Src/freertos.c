/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * File Name          : freertos.c
  * Description        : Code for freertos applications
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2026 STMicroelectronics.
  * All rights reserved.
  *
  * This software is licensed under terms that can be found in the LICENSE file
  * in the root directory of this software component.
  * If no LICENSE file comes with this software, it is provided AS-IS.
  *
  ******************************************************************************
  */
/* USER CODE END Header */

/* Includes ------------------------------------------------------------------*/
#include "FreeRTOS.h"
#include "task.h"
#include "main.h"
#include "cmsis_os.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include "ALLHeader.h"
#include "intsever.h"
#include "runtime_diagnostics.h"
#include "app_runtime.h"

/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */

/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */

/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/
/* USER CODE BEGIN Variables */

/* USER CODE END Variables */
/* Definitions for AppTask */
osThreadId_t ActionTaskHandle;
const osThreadAttr_t ActionTask_attributes = {
  .name = "ActionTask",
  .stack_size = 512 * 4,
  .priority = (osPriority_t) osPriorityAboveNormal,
};

osThreadId_t ReceiveTaskHandle, MonitorTaskHandle, TransmitTaskHandle;
const osThreadAttr_t ReceiveTask_attributes = {
  .name = "ReceiveTask", .stack_size = 1536, .priority = osPriorityHigh
};
const osThreadAttr_t MonitorTask_attributes = {
  .name = "MonitorTask", .stack_size = 1024, .priority = osPriorityAboveNormal
};
const osThreadAttr_t TransmitTask_attributes = {
  .name = "TransmitTask", .stack_size = 1536, .priority = osPriorityNormal
};


/* Private function prototypes -----------------------------------------------*/
/* USER CODE BEGIN FunctionPrototypes */
/* USER CODE END FunctionPrototypes */

void StartAppTask(void *argument);
void StartReceiveTask(void *argument);
void StartMonitorTask(void *argument);
void StartTransmitTask(void *argument);

void MX_FREERTOS_Init(void); /* (MISRA C 2004 rule 8.1) */

/**
  * @brief  FreeRTOS initialization
  * @param  None
  * @retval None
  */
void MX_FREERTOS_Init(void) {
  /* USER CODE BEGIN Init */

  /* USER CODE END Init */

  /* USER CODE BEGIN RTOS_MUTEX */
  /* add mutexes, ... */
  /* USER CODE END RTOS_MUTEX */

  /* USER CODE BEGIN RTOS_SEMAPHORES */
  /* add semaphores, ... */
  /* USER CODE END RTOS_SEMAPHORES */

  /* USER CODE BEGIN RTOS_TIMERS */
  /* start timers, add new ones, ... */
  /* USER CODE END RTOS_TIMERS */

  /* USER CODE BEGIN RTOS_QUEUES */
  AppRuntime_Init();
  /* USER CODE END RTOS_QUEUES */

  /* Create the thread(s) */
  /* creation of AppTask */
  ActionTaskHandle = osThreadNew(StartAppTask, NULL, &ActionTask_attributes);

  /* USER CODE BEGIN RTOS_THREADS */
  if (ActionTaskHandle == NULL) Error_Handler();
  ReceiveTaskHandle = osThreadNew(StartReceiveTask, NULL, &ReceiveTask_attributes);
  MonitorTaskHandle = osThreadNew(StartMonitorTask, NULL, &MonitorTask_attributes);
  TransmitTaskHandle = osThreadNew(StartTransmitTask, NULL, &TransmitTask_attributes);
  if (!ReceiveTaskHandle || !MonitorTaskHandle || !TransmitTaskHandle) Error_Handler();
  /* USER CODE END RTOS_THREADS */

  /* USER CODE BEGIN RTOS_EVENTS */
  /* add events, ... */
  /* USER CODE END RTOS_EVENTS */

}

/* USER CODE BEGIN Header_StartAppTask */
/**
  * @brief  Function implementing the AppTask thread.
  * @param  argument: Not used
  * @retval None
  */
/* USER CODE END Header_StartAppTask */
void StartAppTask(void *argument)
{
  /* USER CODE BEGIN StartAppTask */
  (void)argument;
  OLED_Draw_Line("RTOS: task start", 2, true, true);
  /* Preserve startup standby: only accepted commands may enable the motors. */
  bsp_services_init();
  AppRuntime_SetReady();
  RuntimeDiagnostics_Start();
  MPU6050_EXTI_Init();
  OLED_Draw_Line("waiting command!", 2, true, true);
  for (;;)
  {
    app_user();
    RuntimeDiagnostics_Poll();
    osDelay(2);
  }
  /* USER CODE END StartAppTask */
}

/* Private application code --------------------------------------------------*/
/* USER CODE BEGIN Header_StartReceiveTask */
/* USER CODE END Header_StartReceiveTask */
void StartReceiveTask(void *argument)
{
  /* USER CODE BEGIN StartReceiveTask */
    (void)argument;
    for (;;) {
        if (AppRuntime_IsReady()) {
            ChassisLink_ReceivePoll();
            Bluetooth_ReceivePoll();
        }
        osDelay(2);
    }
  /* USER CODE END StartReceiveTask */
}
/* USER CODE BEGIN Header_StartMonitorTask */
/* USER CODE END Header_StartMonitorTask */
void StartMonitorTask(void *argument)
{
  /* USER CODE BEGIN StartMonitorTask */
    TickType_t wake;
    (void)argument;
    while (!AppRuntime_IsReady()) osDelay(2);
    wake = xTaskGetTickCount();
    for (;;) {
        Monitor_Poll10ms();
        vTaskDelayUntil(&wake, pdMS_TO_TICKS(10));
    }
  /* USER CODE END StartMonitorTask */
}
/* USER CODE BEGIN Header_StartTransmitTask */
/* USER CODE END Header_StartTransmitTask */
void StartTransmitTask(void *argument)
{
  /* USER CODE BEGIN StartTransmitTask */
    (void)argument;
    for (;;) {
        if (AppRuntime_IsReady()) AppRuntime_TransmitPoll();
        osDelay(1);
    }
  /* USER CODE END StartTransmitTask */
}
/* USER CODE BEGIN Application */
void RuntimeDiagnostics_ReportTasks(void)
{
    char report[128];
    snprintf(report, sizeof(report), "DIAG,TASKS,1,%lu,%lu,%lu,%lu,%lu\n",
        (unsigned long)HAL_GetTick(),
        (unsigned long)uxTaskGetStackHighWaterMark(ActionTaskHandle) * sizeof(StackType_t),
        (unsigned long)uxTaskGetStackHighWaterMark(ReceiveTaskHandle) * sizeof(StackType_t),
        (unsigned long)uxTaskGetStackHighWaterMark(MonitorTaskHandle) * sizeof(StackType_t),
        (unsigned long)uxTaskGetStackHighWaterMark(TransmitTaskHandle) * sizeof(StackType_t));
    ChassisLink_SendDiagnostic(report);
    AppRuntime_Report();
}
/* No UART, allocation or RTOS calls here: stop PWM even if the kernel failed. */
void vApplicationStackOverflowHook(TaskHandle_t task, char *name)
{
  (void)task;
  (void)name;
  Error_Handler();
}

void vApplicationMallocFailedHook(void)
{
  Error_Handler();
}

/* USER CODE END Application */

