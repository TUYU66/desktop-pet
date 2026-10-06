#ifndef _NO_AUDIO_CODEC_H
#define _NO_AUDIO_CODEC_H

#include "audio_codec.h"

#include <driver/gpio.h>
#include <mutex>

class NoAudioCodec : public AudioCodec {
protected:
    // 没有外置可配置 Codec 芯片，仍通过 ESP32 I2S 连接数字麦克风和功放。
    // 读写与启停共用一把锁，避免切换 I2S 通道时另一个任务仍在访问句柄。
    std::mutex data_if_mutex_;

    virtual int Write(const int16_t* data, int samples) override;
    virtual int Read(int16_t* dest, int samples) override;
    virtual void EnableInput(bool enable) override;
    virtual void EnableOutput(bool enable) override;

public:
    virtual ~NoAudioCodec();
};

class NoAudioCodecSimplex : public NoAudioCodec {
public:
    // 麦克风与扬声器使用两套独立 I2S 时钟，当前面包板 LCD 配置采用此模式。
    NoAudioCodecSimplex(int input_sample_rate, int output_sample_rate, gpio_num_t spk_bclk, gpio_num_t spk_ws, gpio_num_t spk_dout, gpio_num_t mic_sck, gpio_num_t mic_ws, gpio_num_t mic_din);
};

#endif // _NO_AUDIO_CODEC_H
