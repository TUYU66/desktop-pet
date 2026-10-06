// Adapted from willrnsantana/robo_eyes_esphome (upstream README declares MIT).
// See UPSTREAM.md for attribution and adaptation details.
#pragma once
// =============================================================================
// robo_eyes.h — Animated robot eyes for ESPHome LVGL displays
//
// Drawing strategy: full-size lv_obj_t + LV_EVENT_DRAW_MAIN callback.
// No lv_canvas needed — works with ESPHome's default LVGL build.
//
// Requires: ESPHome >= 2024.11 (LVGL 9), ESP32
// =============================================================================

#include <esp_log.h>


#include "lvgl.h"
#include <cmath>
#include <algorithm>

#include "esp_random.h"

namespace robo_eyes {

// ── Mood ─────────────────────────────────────────────────────────────────────
enum class Mood : uint8_t {
    DEFAULT  = 0,
    HAPPY    = 1,
    ANGRY    = 2,
    TIRED    = 3,
    CURIOUS  = 4,
    CLOSED   = 5,
    CONFUSED = 6,
};

// =============================================================================
class RoboEyes {
   public:
    // ── Setters ───────────────────────────────────────────────────────────────
    void set_eye_color(uint32_t c)        { eye_color_     = c; }
    void set_mouth_color(uint32_t c)      { mouth_color_ = c; }
    void set_bg_color(uint32_t c)         { bg_color_      = c; }
    void set_eye_width(int v)             { eye_width_     = v; }
    void set_eye_height(int v)            { eye_height_    = v; }
    void set_border_radius(int v)         { border_radius_ = v; }
    void set_eye_gap(int v)               { eye_gap_       = v; }
    void set_space_between(int v)         { space_between_ = v; }
    void set_pupil_size(int v)            { pupil_size_    = v; }
    void set_show_pupils(bool v)          { show_pupils_   = v; }
    void set_cyclops(bool v)              { cyclops_       = v; }
    void set_blink_interval(uint32_t ms)  { blink_interval_ms_  = ms; }
    void set_blink_variation(uint32_t ms) { blink_variation_ms_ = ms; }
    void set_wander_interval(uint32_t ms) { wander_interval_ms_ = ms; }
    void set_fps(int v)                   { fps_ = v; }
    void set_display_width(int v)         { disp_w_ = v; }
    void set_display_height(int v)        { disp_h_ = v; }

    void set_phase_idle(int v)            { phase_idle_           = v; }
    void set_phase_listening(int v)       { phase_listening_      = v; }
    void set_phase_thinking(int v)        { phase_thinking_       = v; }
    void set_phase_replying(int v)        { phase_replying_       = v; }
    void set_phase_not_ready(int v)       { phase_not_ready_      = v; }
    void set_phase_error(int v)           { phase_error_          = v; }
    void set_phase_muted(int v)           { phase_muted_          = v; }
    void set_phase_timer_finished(int v)  { phase_timer_finished_ = v; }

    // ── Public API ────────────────────────────────────────────────────────────
    void set_mood(Mood m);
    void set_phase(int phase_id);

    RoboEyes(lv_obj_t* parent, int width, int height);
    ~RoboEyes();
    RoboEyes(const RoboEyes&) = delete;
    RoboEyes& operator=(const RoboEyes&) = delete;
    uint32_t EyeColor() const { return eye_color_; }
    uint32_t MouthColor() const { return mouth_color_; }
    const char* ExpressionName() const { return expression_name_; }
    void SetEmotion(const char* emotion);
    static bool IsExpression(const char* emotion);
    void SetRequestedEmotion(const char* emotion);
    void SetSpeaking(bool speaking);
    void SetIdle(bool idle);
    void SetConnecting(bool connecting) { connecting_ = connecting; }

    // ── LVGL callbacks ────────────────────────────────────────────────────────
    static void s_tick(lv_timer_t *t);
    static void s_draw_cb(lv_event_t *e);
    void tick();
    void draw_frame_(lv_layer_t *layer);

   protected:
    // ── Configuration ─────────────────────────────────────────────────────────
    uint32_t eye_color_         = 0x66CCFF;
    uint32_t mouth_color_       = 0x66CCFF;
    uint32_t bg_color_          = 0x000000;
    int      eye_width_         = 62;
    int      eye_height_        = 46;
    int      border_radius_     = 16;
    int      eye_gap_           = 28;
    int      space_between_     = 0;
    int      pupil_size_        = 10;
    bool     show_pupils_       = false;
    bool     cyclops_           = false;
    uint32_t blink_interval_ms_ = 4000;
    uint32_t blink_variation_ms_= 2000;
    uint32_t wander_interval_ms_= 5000;
    int      fps_               = 25;
    int      disp_w_            = 240;
    int      disp_h_            = 240;

    int phase_idle_           = 1;
    int phase_listening_      = 2;
    int phase_thinking_       = 3;
    int phase_replying_       = 4;
    int phase_not_ready_      = 10;
    int phase_error_          = 11;
    int phase_muted_          = 12;
    int phase_timer_finished_ = 20;

    // ── LVGL objects ──────────────────────────────────────────────────────────
    lv_obj_t   *screen_   = nullptr;   // dedicated LVGL screen
    lv_obj_t   *draw_obj_ = nullptr;   // full-size obj with draw callback
    lv_timer_t *timer_    = nullptr;

    // ── Animation state ───────────────────────────────────────────────────────
    Mood     mood_          = Mood::DEFAULT;
    float    openness_      = 1.0f;
    uint8_t  blink_phase_   = 0;
    uint32_t next_blink_ms_ = 0;
    uint32_t next_wander_ms_= 0;
    float    px_ = 0, py_ = 0;
    float    tpx_= 0, tpy_= 0;
    float    bounce_y_  = 0;
    float    flicker_x_ = 0;
    uint32_t frame_     = 0;
    bool speaking_ = false;
    float mouth_open_ = 0.0f;
    uint32_t speaking_started_ms_ = 0;
    bool attentive_ = false;
    bool thinking_ = false;
    bool reminder_wait_ = false;
    uint32_t acknowledgement_until_ = 0;
    bool dialogue_emotion_ = false;
    uint32_t emotion_hold_until_ = 0;
    bool idle_ = false;
    bool idle_expression_ = false;
    uint32_t next_idle_ms_ = 0;
    uint8_t last_idle_choice_ = 255;
    uint16_t idle_faces_used_ = 0;
    uint32_t idle_started_ms_ = 0;
    uint32_t idle_transition_started_ = 0;
    float idle_face_[8] = {1, 1, .33f, 0, .018f, 0, .13f, 1};
    float idle_from_[8] = {};
    float idle_to_[8] = {};
    bool idle_transition_ = false;
    float peek_pupil_visibility_ = 0.0f;
    enum class Effect { NONE, HEARTS, SLEEP, STARS, ANGRY, BLUSH, CONFETTI, DAYDREAM, QUESTION, RAYS, PEEK, BUBBLES, BREATH, MUSIC, TEARS };
    Effect effect_ = Effect::NONE;
    uint32_t effect_started_ms_ = 0;
    uint32_t effect_duration_ms_ = 0;
    bool connecting_ = false;
    struct Expression { const char* name; Mood mood; Effect effect; float pose[8]; int gaze; };
    static const Expression* FindExpression(const char* name);
    bool ApplyExpression(const char* name);
    const char* expression_name_ = "neutral";
    int expression_gaze_ = 0;
    uint32_t requested_until_ = 0;
    void StartEffect(Effect effect, uint32_t duration);
    void draw_effects_(lv_layer_t* layer, const lv_area_t& bounds);

    // ── Helpers ───────────────────────────────────────────────────────────────
    void draw_eye_(lv_layer_t *layer, float cx, float cy,
                   float ew, float vis_h, bool is_left);
    void draw_mouth_(lv_layer_t* layer, float cx, float cy);

    static inline float lerp_(float a, float b, float t) { return a + (b - a) * t; }
    static inline float clamp_(float v, float lo, float hi) {
        return v < lo ? lo : (v > hi ? hi : v);
    }
    static uint32_t rng_() {
        return esp_random();
    }
    inline uint32_t millis_() const { return (uint32_t) lv_tick_get(); }
};


} // namespace robo_eyes
