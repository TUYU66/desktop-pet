#include "no_audio_codec.h"

#include <esp_log.h>
#include <cmath>
#include <cstring>
#include <esp_heap_caps.h>

#define TAG "NoAudioCodec"

NoAudioCodec::~NoAudioCodec() {
    if (rx_handle_ != nullptr) {
        ESP_ERROR_CHECK(i2s_channel_disable(rx_handle_));
    }
    if (tx_handle_ != nullptr) {
        ESP_ERROR_CHECK(i2s_channel_disable(tx_handle_));
    }
}

NoAudioCodecSimplex::NoAudioCodecSimplex(int input_sample_rate, int output_sample_rate, gpio_num_t spk_bclk, gpio_num_t spk_ws, gpio_num_t spk_dout, gpio_num_t mic_sck, gpio_num_t mic_ws, gpio_num_t mic_din) {
    // 单工分离模式：扬声器 TX 与麦克风 RX 分别配置引脚，适合当前外接功放/数字麦克风。
    duplex_ = false;
    input_sample_rate_ = input_sample_rate;
    output_sample_rate_ = output_sample_rate;

    // Create a new channel for speaker
    i2s_chan_config_t chan_cfg = {
        .id = (i2s_port_t)0,
        .role = I2S_ROLE_MASTER,
        .dma_desc_num = AUDIO_CODEC_DMA_DESC_NUM,
        .dma_frame_num = AUDIO_CODEC_DMA_FRAME_NUM,
        .auto_clear_after_cb = true,
        .auto_clear_before_cb = false,
        .intr_priority = 0,
    };
    ESP_ERROR_CHECK(i2s_new_channel(&chan_cfg, &tx_handle_, nullptr));

    i2s_std_config_t std_cfg = {
        .clk_cfg = {
            .sample_rate_hz = (uint32_t)output_sample_rate_,
            .clk_src = I2S_CLK_SRC_DEFAULT,
            .mclk_multiple = I2S_MCLK_MULTIPLE_256,
			#ifdef   I2S_HW_VERSION_2
				.ext_clk_freq_hz = 0,
			#endif

        },
        .slot_cfg = {
            .data_bit_width = I2S_DATA_BIT_WIDTH_32BIT,
            .slot_bit_width = I2S_SLOT_BIT_WIDTH_AUTO,
            .slot_mode = I2S_SLOT_MODE_MONO,
            .slot_mask = I2S_STD_SLOT_LEFT,
            .ws_width = I2S_DATA_BIT_WIDTH_32BIT,
            .ws_pol = false,
            .bit_shift = true,
            #ifdef   I2S_HW_VERSION_2
                .left_align = true,
                .big_endian = false,
                .bit_order_lsb = false
            #endif

        },
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = spk_bclk,
            .ws = spk_ws,
            .dout = spk_dout,
            .din = I2S_GPIO_UNUSED,
            .invert_flags = {
                .mclk_inv = false,
                .bclk_inv = false,
                .ws_inv = false
            }
        }
    };
    ESP_ERROR_CHECK(i2s_channel_init_std_mode(tx_handle_, &std_cfg));

    // Create a new channel for MIC
    chan_cfg.id = (i2s_port_t)1;
    ESP_ERROR_CHECK(i2s_new_channel(&chan_cfg, nullptr, &rx_handle_));
    std_cfg.clk_cfg.sample_rate_hz = (uint32_t)input_sample_rate_;
    std_cfg.gpio_cfg.bclk = mic_sck;
    std_cfg.gpio_cfg.ws = mic_ws;
    std_cfg.gpio_cfg.dout = I2S_GPIO_UNUSED;
    std_cfg.gpio_cfg.din = mic_din;
    ESP_ERROR_CHECK(i2s_channel_init_std_mode(rx_handle_, &std_cfg));
    ESP_LOGI(TAG, "Simplex channels created");
}

int NoAudioCodec::Write(const int16_t* data, int samples) {
    // samples 是 16 位采样点数量，不是字节数；返回值也统一换算为采样点数量。
    if (data == nullptr || samples <= 0) return 0;
    std::lock_guard<std::mutex> lock(data_if_mutex_);

    // output_volume_: 0-100
    // volume_factor_: 0-65536
    int32_t volume_factor = pow(double(output_volume_) / 100.0, 2) * 65536;

    // I2S DMA 需要 buffer 在内部 SRAM 中，不能放在 PSRAM
    // 使用 DMA-capable 的内部内存分配
    size_t buf_size = samples * sizeof(int32_t);
    int32_t* buffer = (int32_t*)heap_caps_malloc(buf_size, MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA);
    if (buffer == nullptr) {
        ESP_LOGE(TAG, "Failed to allocate I2S DMA buffer (%u bytes)", buf_size);
        return 0;
    }

    for (int i = 0; i < samples; i++) {
        int64_t temp = int64_t(data[i]) * volume_factor;
        if (temp > INT32_MAX) {
            buffer[i] = INT32_MAX;
        } else if (temp < INT32_MIN) {
            buffer[i] = INT32_MIN;
        } else {
            buffer[i] = static_cast<int32_t>(temp);
        }
    }

    size_t bytes_written = 0;
    auto ret = i2s_channel_write(tx_handle_, buffer, buf_size, &bytes_written, 500);
    heap_caps_free(buffer);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "I2S write failed: %d (written=%u/%u bytes)", ret,
            static_cast<unsigned>(bytes_written), static_cast<unsigned>(buf_size));
    } else if (bytes_written != buf_size) {
        ESP_LOGW(TAG, "I2S short write: %u/%u bytes",
            static_cast<unsigned>(bytes_written), static_cast<unsigned>(buf_size));
    }
    return bytes_written / sizeof(int32_t);
}

int NoAudioCodec::Read(int16_t* dest, int samples) {
    // I2S 读取可能短读，调用者根据实际返回采样点继续处理。
    size_t bytes_read;
    constexpr TickType_t kReadTimeoutTicks = pdMS_TO_TICKS(200);

    std::vector<int32_t> bit32_buffer(samples);
    if (i2s_channel_read(rx_handle_, bit32_buffer.data(), samples * sizeof(int32_t), &bytes_read, kReadTimeoutTicks) != ESP_OK) {
        return 0;
    }

    samples = bytes_read / sizeof(int32_t);
    for (int i = 0; i < samples; i++) {
        // Keep the conversion linear for AEC. The old >>12 adds 24 dB and
        // clips speaker echo before the echo canceller can remove it.
        int32_t value = bit32_buffer[i] >> 16;
        dest[i] = (value > INT16_MAX) ? INT16_MAX : (value < -INT16_MAX) ? -INT16_MAX : (int16_t)value;
    }
    return samples;
}

void NoAudioCodec::EnableInput(bool enable) {
    // 启停 RX 通道时持锁，避免 AudioInputTask 正在读取同一个句柄。
    std::lock_guard<std::mutex> lock(data_if_mutex_);
    if (enable == input_enabled_) {
        return;
    }
    if (enable) {
        auto ret = i2s_channel_enable(rx_handle_);
        if (ret != ESP_OK) {
            ESP_LOGE(TAG, "I2S RX enable failed: %d", ret);
            return;
        }
    } else {
        auto ret = i2s_channel_disable(rx_handle_);
        if (ret != ESP_OK) {
            ESP_LOGE(TAG, "I2S RX disable failed: %d", ret);
            return;
        }
    }
    AudioCodec::EnableInput(enable);
}

void NoAudioCodec::EnableOutput(bool enable) {
    // 停止 TX 前先让上层清空播放队列，减少截断音；这里仅负责硬件通道。
    std::lock_guard<std::mutex> lock(data_if_mutex_);
    if (enable == output_enabled_) {
        return;
    }
    if (enable) {
        auto ret = i2s_channel_enable(tx_handle_);
        if (ret != ESP_OK) {
            ESP_LOGE(TAG, "I2S TX enable failed: %d", ret);
            return;
        }
    } else {
        auto ret = i2s_channel_disable(tx_handle_);
        if (ret != ESP_OK) {
            ESP_LOGE(TAG, "I2S TX disable failed: %d", ret);
            return;
        }
    }
    AudioCodec::EnableOutput(enable);
}
