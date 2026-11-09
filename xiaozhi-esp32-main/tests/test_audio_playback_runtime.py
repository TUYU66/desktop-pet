"""Exercise production playback methods with fake I2S/AFE on a host C++ compiler.

Run with CXX=g++ (or an absolute compiler path). No ESP32 hardware is accessed.
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def method(relative, signature):
    source = (ROOT / relative).read_text(encoding='utf-8')
    start = source.index(signature)
    opening = source.index('{', start)
    depth, end = 1, opening + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


HARNESS = r'''
#include <algorithm>
#include <atomic>
#include <cassert>
#include <climits>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <future>
#include <iostream>
#include <mutex>
#include <vector>
#define ESP_LOGE(...) ((void)0)
#define MALLOC_CAP_INTERNAL 1
#define MALLOC_CAP_DMA 2
#define ESP_OK 0
int allocations = 0;
bool allocation_failure = false;
void* heap_caps_malloc(size_t size, int) {
    ++allocations;
    return allocation_failure ? nullptr : std::malloc(size);
}
std::vector<uint8_t> output;
size_t write_limit = 100;
int write_error = 0;
int i2s_channel_write(void*, const void* data, size_t size, size_t* written, int) {
    *written = std::min(size, write_limit);
    const auto* bytes = static_cast<const uint8_t*>(data);
    output.insert(output.end(), bytes, bytes + *written);
    return write_error;
}
class NoAudioCodec {
public:
    std::mutex data_if_mutex_;
    static constexpr size_t kOutputChunkSamples = 240;
    int32_t* output_buffer_ = nullptr;
    int output_volume_ = 100;
    void* tx_handle_ = nullptr;
    int Write(const int16_t*, int);
    ~NoAudioCodec() { std::free(output_buffer_); }
};
constexpr size_t NoAudioCodec::kOutputChunkSamples;
struct BlockingFeed {
    std::promise<void> entered;
    std::shared_future<void> release;
};
struct FakeAfe {
    size_t get_feed_chunksize(void*) { return 512; }
    void feed(BlockingFeed* state, const int16_t*) {
        state->entered.set_value();
        state->release.wait();
    }
};
struct FakeGate { void AddReference(const int16_t*, size_t, int64_t, size_t = 1) {} };
int64_t esp_timer_get_time() { return 1000000; }
class AfeAudioProcessor {
public:
    std::mutex input_buffer_mutex_, playback_reference_mutex_;
    std::vector<int16_t> input_buffer_;
    std::deque<int16_t> playback_reference_;
    bool software_reference_ = true;
    int feed_channels_ = 2;
    int64_t last_reference_us_ = 0;
    FakeAfe* afe_iface_;
    BlockingFeed* afe_data_;
    FakeGate barge_in_gate_;
    struct Codec { bool input_reference() { return false; } int input_channels() { return 1; } } *codec_ = nullptr;
    bool IsRunning() { return true; }
    void Feed(std::vector<int16_t>&&);
    void FeedPlaybackReference(const std::vector<int16_t>&);
};
'''

CHECKS = r'''
int main() {
    NoAudioCodec codec;
    std::vector<int16_t> samples(500);
    for (size_t i = 0; i < samples.size(); ++i) samples[i] = int16_t(i * 73 - 16000);
    assert(codec.Write(samples.data(), samples.size()) == 500);
    assert(output.size() == samples.size() * sizeof(int32_t));
    for (size_t i = 0; i < samples.size(); ++i) {
        int32_t actual;
        std::memcpy(&actual, output.data() + i * 4, 4);
        assert(actual == int64_t(samples[i]) * 65536);
    }
    assert(allocations == 1);
    output.clear();
    codec.output_volume_ = 50;
    assert(codec.Write(samples.data(), 1) == 1);
    int32_t attenuated;
    std::memcpy(&attenuated, output.data(), 4);
    assert(attenuated == int64_t(samples[0]) * 16384);
    assert(allocations == 1); // playback reuses the allocation
    assert(codec.Write(nullptr, 100) == 0);
    write_error = -1; write_limit = 4;
    assert(codec.Write(samples.data(), 100) == 1); // bounded partial timeout
    write_error = 0; write_limit = 0;
    assert(codec.Write(samples.data(), 100) == 0); // no zero-progress loop
    allocation_failure = true;
    NoAudioCodec no_memory;
    assert(no_memory.Write(samples.data(), 100) == 0);
    allocation_failure = false;

    FakeAfe afe;
    BlockingFeed state;
    std::promise<void> release;
    state.release = release.get_future().share();
    AfeAudioProcessor processor;
    processor.afe_iface_ = &afe; processor.afe_data_ = &state;
    auto feed = std::async(std::launch::async, [&] { processor.Feed(std::vector<int16_t>(512, 1)); });
    state.entered.get_future().wait();
    auto playback = std::async(std::launch::async, [&] { processor.FeedPlaybackReference(std::vector<int16_t>(960, 5)); });
    const bool playback_ready = playback.wait_for(std::chrono::seconds(1)) == std::future_status::ready;
    release.set_value(); // cleanly release both threads, including on regression
    feed.get(); playback.get();
    assert(playback_ready); // must not wait for the blocked input AFE feed
    assert(processor.playback_reference_.size() == 960);
    std::cout << "I2S continuity, buffer reuse and blocked-AFE playback isolation passed\n";
}
'''


class PlaybackRuntimeTest(unittest.TestCase):
    def test_i2s_continuity_and_playback_isolation(self):
        compiler = shutil.which(os.environ.get('CXX', 'g++'))
        if not compiler:
            self.skipTest('Set CXX to a host C++ compiler')
        source = HARNESS + '\n' + '\n'.join([
            method('main/audio/codecs/no_audio_codec.cc', 'int NoAudioCodec::Write('),
            method('main/audio/processors/afe_audio_processor.cc', 'void AfeAudioProcessor::Feed('),
            method('main/audio/processors/afe_audio_processor.cc', 'void AfeAudioProcessor::FeedPlaybackReference('),
        ]) + CHECKS
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            code, executable = folder / 'playback.cc', folder / 'playback-test.exe'
            code.write_text(source, encoding='utf-8')
            subprocess.run([compiler, '-std=c++14', '-pthread', str(code), '-o', str(executable)], check=True, timeout=60)
            environment = dict(os.environ)
            environment['PATH'] = str(Path(compiler).parent) + os.pathsep + environment.get('PATH', '')
            subprocess.run([str(executable)], check=True, timeout=10, env=environment)


if __name__ == '__main__':
    unittest.main()
