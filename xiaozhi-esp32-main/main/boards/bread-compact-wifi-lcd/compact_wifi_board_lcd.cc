#include "wifi_board.h"
#include "codecs/no_audio_codec.h"
#include "display/lcd_display.h"
#include "application.h"
#include "button.h"
#include "config.h"
#include "mcp_server.h"
#include "led/single_led.h"
#include "stm32_link.h"

#include <esp_log.h>
#include <esp_lcd_panel_vendor.h>
#include <esp_lcd_panel_io.h>
#include <esp_lcd_panel_ops.h>
#include <driver/spi_common.h>

#define TAG "CompactWifiBoardLCD"

class CompactWifiBoardLCD : public WifiBoard {
private:
    // 当前板级类只保存按键和显示对象；音频、背光、LED 使用函数内静态单例。
    Button boot_button_;
    LcdDisplay* display_;

    void InitializeSpi() {
        // LCD 独占 SPI3_HOST，并启用 DMA；最大传输尺寸按整屏 RGB565 计算。
        spi_bus_config_t buscfg = {};
        buscfg.mosi_io_num = DISPLAY_MOSI_PIN;
        buscfg.miso_io_num = GPIO_NUM_NC;
        buscfg.sclk_io_num = DISPLAY_CLK_PIN;
        buscfg.quadwp_io_num = GPIO_NUM_NC;
        buscfg.quadhd_io_num = GPIO_NUM_NC;
        buscfg.max_transfer_sz = DISPLAY_WIDTH * DISPLAY_HEIGHT * sizeof(uint16_t);
        ESP_ERROR_CHECK(spi_bus_initialize(SPI3_HOST, &buscfg, SPI_DMA_CH_AUTO));
    }

    void InitializeLcdDisplay() {
        esp_lcd_panel_io_handle_t panel_io = nullptr;
        esp_lcd_panel_handle_t panel = nullptr;
        // 创建 SPI 面板 IO：负责 CS/DC、命令宽度和传输队列。
        ESP_LOGD(TAG, "Install panel IO");
        esp_lcd_panel_io_spi_config_t io_config = {};
        io_config.cs_gpio_num = DISPLAY_CS_PIN;
        io_config.dc_gpio_num = DISPLAY_DC_PIN;
        io_config.spi_mode = DISPLAY_SPI_MODE;
        io_config.pclk_hz = 40 * 1000 * 1000;
        io_config.trans_queue_depth = 10;
        io_config.lcd_cmd_bits = 8;
        io_config.lcd_param_bits = 8;
        ESP_ERROR_CHECK(esp_lcd_new_panel_io_spi(SPI3_HOST, &io_config, &panel_io));

        // 当前硬件固定使用 ST7789 240x240 控制器。
        ESP_LOGD(TAG, "Install LCD driver");
        esp_lcd_panel_dev_config_t panel_config = {};
        panel_config.reset_gpio_num = DISPLAY_RST_PIN;
        panel_config.rgb_ele_order = DISPLAY_RGB_ORDER;
        panel_config.bits_per_pixel = 16;
        ESP_ERROR_CHECK(esp_lcd_new_panel_st7789(panel_io, &panel_config, &panel));

        esp_lcd_panel_reset(panel);

        // 初始化后应用颜色反相、横竖屏交换、镜像和可视区域偏移。
        esp_lcd_panel_init(panel);
        esp_lcd_panel_invert_color(panel, DISPLAY_INVERT_COLOR);
        esp_lcd_panel_swap_xy(panel, DISPLAY_SWAP_XY);
        esp_lcd_panel_mirror(panel, DISPLAY_MIRROR_X, DISPLAY_MIRROR_Y);
        display_ = new SpiLcdDisplay(panel_io, panel,
                                    DISPLAY_WIDTH, DISPLAY_HEIGHT, DISPLAY_OFFSET_X, DISPLAY_OFFSET_Y, DISPLAY_MIRROR_X, DISPLAY_MIRROR_Y, DISPLAY_SWAP_XY);
    }

    void InitializeButtons() {
        // 启动阶段按键进入配网；正常运行时同一按键切换对话状态。
        boot_button_.OnClick([this]() {
            auto& app = Application::GetInstance();
            if (app.GetDeviceState() == kDeviceStateStarting) {
                EnterWifiConfigMode();
                return;
            }
            app.ToggleChatState();
        });
    }

    void InitializeTools() {
        auto& mcp = McpServer::GetInstance();
        mcp.AddTool("self.dashboard.get_state", "Read actual voice wake/standby mode and fresh chassis posture, plus control panel state. This is read-only. Wake mode and standing/seated posture are independent; unknown or moving posture is not a completed action.",
            PropertyList(), [this](const PropertyList&) -> ReturnValue {
                return "{\"deviceState\":" + std::to_string(static_cast<int>(Application::GetInstance().GetDeviceState())) +
                    ",\"conversationAwake\":" + (Application::GetInstance().IsConversationAwake() ? "true" : "false") +
                    ",\"hardware\":" + GetDeviceStatusJson() +
                    ",\"chassis\":" + Stm32Link::GetInstance().GetStatusJson() +
                    ",\"face\":" + display_->GetFaceStateJson() + "}";
            });
        mcp.AddTool("self.chassis.get_status", "Read the STM32 car board UART connection, battery voltage and balance stop state. This tool does not move the car. Only explain voltage or hardware details when the user explicitly requests them; for ordinary battery questions use self.chassis.get_battery.",
            PropertyList(), [](const PropertyList&) -> ReturnValue {
                return Stm32Link::GetInstance().GetStatusJson();
            });
        mcp.AddTool("self.chassis.get_battery",
            "用户询问电量、还剩多少电时调用。用一句自然的话回答，例如‘电量大约还剩91%’，低电量时提醒充电。保留‘大约’，不要主动解释实测电压、底盘、STM32、估算原理或行驶波动。用户明确问电压时使用 self.chassis.get_status。",
            PropertyList(), [](const PropertyList&) -> ReturnValue {
                return Stm32Link::GetInstance().GetBatteryText();
            });
        // Hidden from the conversational tools list: only the manual web panel calls it.
        mcp.AddUserOnlyTool("self.chassis.tuning_state", "Read-only bounded PID state and telemetry. Operator UI only.",
            PropertyList({Property("after", kPropertyTypeInteger, 0, 0, 2147483647),
                          Property("epoch", kPropertyTypeInteger, 0, 0, 2147483647)}),
            [](const PropertyList& properties) -> ReturnValue {
                return std::string("{\"ok\":true,\"state\":")+
                    Stm32Link::GetInstance().GetTuningJson(properties["after"].value<int>(),properties["epoch"].value<int>())+"}";
            });
        mcp.AddUserOnlyTool("self.chassis.tuning", "Manual PID tuning, stopped gyro zero calibration and telemetry. Operator only.",
            PropertyList({Property("operation", kPropertyTypeString),
                          Property("revision", kPropertyTypeInteger, 0, 0, 2147483646),
                          Property("sample_tick", kPropertyTypeInteger, 0, 0, 2147483647),
                          Property("values", kPropertyTypeString, std::string(""))}),
            [](const PropertyList& properties) -> ReturnValue {
                auto& link = Stm32Link::GetInstance();
                const auto operation=properties["operation"].value<std::string>();
                return link.RequestTuning(operation,static_cast<unsigned int>(properties["revision"].value<int>()),
                    static_cast<unsigned int>(properties["sample_tick"].value<int>()),properties["values"].value<std::string>());
            }, true);
        mcp.AddUserOnlyTool("self.chassis.calibration", "Manual bench motor calibration. Operator only; never infer from conversation.",
            PropertyList({Property("operation", kPropertyTypeString),
                          Property("wheel", kPropertyTypeInteger, 1, 1, 2),
                          Property("pwm", kPropertyTypeInteger, 0, -2600, 2600),
                          Property("confirmed", kPropertyTypeBoolean, false),
                          Property("session", kPropertyTypeInteger, 0, 0, 65535),
                          Property("sample_tick", kPropertyTypeInteger, 0, 0, 2147483647)}),
            [](const PropertyList& properties) -> ReturnValue {
                return Stm32Link::GetInstance().RequestCalibration(properties["operation"].value<std::string>(),
                    properties["wheel"].value<int>(), properties["pwm"].value<int>(), properties["confirmed"].value<bool>(),
                    static_cast<unsigned int>(properties["session"].value<int>()),
                    static_cast<unsigned int>(properties["sample_tick"].value<int>()));
            }, true);
        mcp.AddAsyncTool("self.chassis.stand_up",
            "用户表达让你从后支架起来、站起、起身、直立等身体动作意图时调用一次；按语义判断，不要求用户说固定指令词。身体控制板在本地检查电池、倾角和姿态采样。动作由工具回复播报完成后执行，不要追加进度话或自行重试。",
            PropertyList(), [](const PropertyList&) -> ReturnValue {
                return Stm32Link::GetInstance().RequestStand();
            });
        mcp.AddAsyncTool("self.chassis.bluetooth_on",
            "用户要求蓝牙控制、开启蓝牙控制或手机遥控时调用一次。坐在后支架上时先自动起立，已站立时保持站立，确认站稳后允许手机App控制移动。设备会记住本次开启前的姿态，重复开启不改变它。手机蓝牙配对由用户在App完成。不要额外调用起立工具；必须根据工具完成结果回答，不提前声称开启成功。开启后语音起立/休息/转向被阻止。",
            PropertyList(), [](const PropertyList&) -> ReturnValue {
                return Stm32Link::GetInstance().RequestBluetooth(true);
            });
        mcp.AddAsyncTool("self.chassis.bluetooth_off",
            "用户要求断开连接、断开蓝牙、关闭蓝牙控制或退出手机遥控时调用一次。设备立即禁止手机移动指令。本次从坐下状态开启蓝牙的，关闭后自动坐回后支架；从站立状态开启的，关闭后保持站立。不要额外调用休息工具。必须根据完成结果回答，不能提前声称已断开；手机App的蓝牙配对连接可能仍显示存在，但移动控制已关闭。",
            PropertyList(), [](const PropertyList&) -> ReturnValue {
                return Stm32Link::GetInstance().RequestBluetooth(false);
            });
        mcp.AddAsyncTool("self.chassis.rest",
            "用户表达让你坐下、坐下来、躺下、靠回后支架、放松休息等身体动作意图时调用一次；按语义判断，不要求用户说固定指令词。身体控制板小幅后倾后停电机，由支架托住。动作由工具回复播报完成后执行，不要追加进度话或自行重试。",
            PropertyList(), [](const PropertyList&) -> ReturnValue {
                return Stm32Link::GetInstance().RequestRest();
            });
        mcp.AddAsyncTool("self.chassis.turn_left",
            "用户明确要求身体向左转、左转身时调用。默认原地约90度；若靠在后支架上，身体控制板会先起立站稳，再转向、站稳并坐回支架。已站立则转后保持站立。完成确认由设备返回；不要用于网页方向或其他设备。",
            PropertyList(), [](const PropertyList&) -> ReturnValue {
                return Stm32Link::GetInstance().RequestTurnLeft();
            });
        mcp.AddAsyncTool("self.chassis.turn_right",
            "用户明确要求身体向右转、右转身时调用。默认原地约90度；若靠在后支架上，身体控制板会先起立站稳，再转向、站稳并坐回支架。已站立则转后保持站立。完成确认由设备返回；不要用于网页方向或其他设备。",
            PropertyList(), [](const PropertyList&) -> ReturnValue {
                return Stm32Link::GetInstance().RequestTurnRight();
            });
        mcp.AddAsyncTool("self.chassis.turn_around",
            "用户明确要求身体向后转、转身朝后、掉头时调用。默认向右原地约180度；若靠在后支架上，身体控制板会先起立站稳，再转向、站稳并坐回支架。已站立则转后保持站立。完成确认由设备返回。",
            PropertyList(), [](const PropertyList&) -> ReturnValue {
                return Stm32Link::GetInstance().RequestTurnAround();
            });
        mcp.AddTool("self.face.set_expression",
            "Show a robot facial expression on user request. emotion: neutral, happy, laughing, loving, angry, sad, crying, tired, sleepy, confused, curious, surprised, shy, embarrassed, confident, winking, peek_left, peek_right, daydream, yawn. Same expressions as idle and chat, with effects except neutral. Held for 8 seconds; music and reminders may replace it. Does not start listening, change playback or put the device to sleep.",
            PropertyList({Property("emotion", kPropertyTypeString)}),
            [this](const PropertyList& properties) -> ReturnValue {
                return display_->SetFaceExpression(properties["emotion"].value<std::string>());
            });
        mcp.AddTool("self.face.set_colors",
            "Change eye and mouth colors independently on user request. RGB integer 0..16777215 (0xRRGGBB); omit a part or use -1 to leave it unchanged. Examples red=16711680, green=65280, blue=255, white=16777215, pink=16738740. Restore default: both=6737151 (0x66CCFF). Colors are saved on the device and restored after reboot; black=0 makes the part invisible on the black background.",
            PropertyList({Property("eye_color", kPropertyTypeInteger, -1, -1, 16777215),
                          Property("mouth_color", kPropertyTypeInteger, -1, -1, 16777215)}),
            [this](const PropertyList& properties) -> ReturnValue {
                return display_->SetFaceColors(properties["eye_color"].value<int>(), properties["mouth_color"].value<int>());
            });
    }

public:
    bool UsesBatteryProtectionFlag() const override { return true; }
    bool IsChassisConnected() const override { return Stm32Link::GetInstance().IsConnected(); }

    bool GetLowBatteryFlag(bool& low) override {
        return Stm32Link::GetInstance().GetLowBattery(low);
    }

    std::string GetMotionHint(bool& busy) override {
        auto hint = Stm32Link::GetInstance().GetMotionHint(busy);
        busy = busy || McpServer::GetInstance().HasAsyncCall();
        return hint;
    }

    bool GetBatteryLevel(int& level, bool& charging, bool& discharging) override {
        charging = false;  // J13 reports voltage, not charge-controller state.
        discharging = false;
        return Stm32Link::GetInstance().GetBatteryLevel(level);
    }

    CompactWifiBoardLCD() :
        boot_button_(BOOT_BUTTON_GPIO) {
        // 板级初始化顺序：总线 -> 显示 -> 按键 -> MCP 外设 -> 恢复背光。
        InitializeSpi();
        InitializeLcdDisplay();
        InitializeButtons();
        InitializeTools();
        Stm32Link::GetInstance().Start();
        if (DISPLAY_BACKLIGHT_PIN != GPIO_NUM_NC) {
            GetBacklight()->RestoreBrightness();
        }

    }

    virtual Led* GetLed() override {
        static SingleLed led(BUILTIN_LED_GPIO);
        return &led;
    }

    virtual AudioCodec* GetAudioCodec() override {
        // 当前项目启用 simplex：麦克风和扬声器使用 config.h 中两套独立引脚。
        static NoAudioCodecSimplex audio_codec(AUDIO_INPUT_SAMPLE_RATE, AUDIO_OUTPUT_SAMPLE_RATE,
            AUDIO_I2S_SPK_GPIO_BCLK, AUDIO_I2S_SPK_GPIO_LRCK, AUDIO_I2S_SPK_GPIO_DOUT, AUDIO_I2S_MIC_GPIO_SCK, AUDIO_I2S_MIC_GPIO_WS, AUDIO_I2S_MIC_GPIO_DIN);
        return &audio_codec;
    }

    virtual Display* GetDisplay() override {
        return display_;
    }

    virtual Backlight* GetBacklight() override {
        // 背光引脚存在时才创建 PWM 控制器；无背光板型返回 nullptr。
        if (DISPLAY_BACKLIGHT_PIN != GPIO_NUM_NC) {
            static PwmBacklight backlight(DISPLAY_BACKLIGHT_PIN, DISPLAY_BACKLIGHT_OUTPUT_INVERT);
            return &backlight;
        }
        return nullptr;
    }
};

DECLARE_BOARD(CompactWifiBoardLCD);
