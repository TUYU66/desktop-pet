// Adapted from willrnsantana/robo_eyes_esphome (upstream README declares MIT).
// See UPSTREAM.md for attribution and adaptation details.
// =============================================================================
// robo_eyes.cpp — Animated robot eyes for ESPHome LVGL displays
//
// Drawing via LV_EVENT_DRAW_MAIN on a full-size lv_obj_t.
// No lv_canvas required. Fixes: lv_draw_triangle_dsc_t uses .color/.opa.
// =============================================================================
#include "robo_eyes.h"
#include "sdkconfig.h"
#include <cstring>

namespace robo_eyes {

static const char *const TAG = "robo_eyes";

// =============================================================================
// setup()
// =============================================================================
RoboEyes::RoboEyes(lv_obj_t* parent, int width, int height) {
    disp_w_ = width;
    disp_h_ = height;
    eye_width_ = std::max(12, width * 26 / 100);
    eye_height_ = std::max(8, std::min(height * 40 / 100, width * 23 / 100));
    eye_gap_ = width * 12 / 100;
    border_radius_ = eye_width_ / 3;
    screen_ = lv_obj_get_screen(parent);

    // ── Full-size drawing object with custom draw callback ────────────────────
    // Using lv_obj instead of lv_canvas — works with ESPHome's default LVGL
    // build which does not enable LV_USE_CANVAS.
    draw_obj_ = lv_obj_create(parent);
    lv_obj_set_size(draw_obj_, disp_w_, disp_h_);
    lv_obj_set_pos(draw_obj_, 0, 0);
    lv_obj_set_style_bg_color(draw_obj_, lv_color_hex(bg_color_), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(draw_obj_, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_pad_all(draw_obj_, 0, LV_PART_MAIN);
    lv_obj_set_style_border_width(draw_obj_, 0, LV_PART_MAIN);
    lv_obj_set_style_radius(draw_obj_, 0, LV_PART_MAIN);
    lv_obj_clear_flag(draw_obj_, LV_OBJ_FLAG_SCROLLABLE);

    // Register draw callback — called by LVGL whenever draw_obj_ is invalidated
    lv_obj_add_event_cb(draw_obj_, s_draw_cb, LV_EVENT_DRAW_MAIN, this);

    // ── Seed animation timers ─────────────────────────────────────────────────
    uint32_t now     = millis_();
    next_blink_ms_   = now + blink_interval_ms_;
    next_wander_ms_  = now + wander_interval_ms_;

    // ── LVGL animation timer ──────────────────────────────────────────────────
    uint32_t period = (fps_ > 0) ? (1000u / (uint32_t) fps_) : 40u;
    timer_ = lv_timer_create(s_tick, period, this);

    ESP_LOGI(TAG, "RoboEyes ready — period %u ms", (unsigned) period);
}

// =============================================================================
// Public API
// =============================================================================
RoboEyes::~RoboEyes() {
    // Owner holds the display lock and destroys us before the parent object.
    if (timer_) lv_timer_delete(timer_);
    if (draw_obj_) lv_obj_delete(draw_obj_);
}

const RoboEyes::Expression* RoboEyes::FindExpression(const char* name) {
    if (!name) return nullptr;
    if (!strcmp(name, "relaxed") || !strcmp(name, "speaking")) name = "neutral";
    if (!strcmp(name, "funny")) name = "laughing";
    if (!strcmp(name, "embarrassed")) name = "shy";
    if (!strcmp(name, "shocked")) name = "surprised";
    if (!strcmp(name, "closed")) name = "sleepy";
    if (!strcmp(name, "silly")) name = "laughing";
    if (!strcmp(name, "cool")) name = "confident";
    if (!strcmp(name, "delicious") || !strcmp(name, "kissy")) name = "loving";
    static const Expression expressions[] = {
        {"neutral", Mood::DEFAULT, Effect::NONE, {1,1,.33f,0,.018f,0,.13f,1}, 0},
        {"happy", Mood::DEFAULT, Effect::STARS, {.85f,.85f,.45f,.42f,.055f,0,.18f,1}, 0},
        {"curious", Mood::DEFAULT, Effect::QUESTION, {1.08f,.75f,.38f,0,0,.30f,.09f,.95f}, 0},
        {"surprised", Mood::DEFAULT, Effect::RAYS, {1.15f,1.15f,.50f,0,0,.70f,.09f,.80f}, 0},
        {"loving", Mood::DEFAULT, Effect::HEARTS, {.55f,.55f,.45f,.18f,.035f,0,.15f,1.05f}, 0},
        {"laughing", Mood::DEFAULT, Effect::STARS, {.90f,.90f,.48f,.38f,.045f,.65f,.19f,1}, 0},
        {"peek_left", Mood::DEFAULT, Effect::PEEK, {1,1,.50f,0,.018f,0,.13f,.88f}, -1},
        {"peek_right", Mood::DEFAULT, Effect::PEEK, {1,1,.50f,0,.018f,0,.13f,.88f}, 1},
        {"daydream", Mood::DEFAULT, Effect::DAYDREAM, {.60f,.60f,.18f,0,0,0,.08f,1}, 2},
        {"sleepy", Mood::DEFAULT, Effect::SLEEP, {.45f,.45f,.35f,0,0,.20f,.10f,1}, 0},
        {"yawn", Mood::DEFAULT, Effect::BREATH, {.06f,.06f,.40f,0,0,1,.14f,1}, 0},
        {"shy", Mood::DEFAULT, Effect::BLUSH, {.85f,.85f,.45f,.35f,.04f,0,.15f,1}, 0},
        {"sad", Mood::TIRED, Effect::TEARS, {1,1,.33f,0,-.035f,0,.13f,1}, 0},
        {"crying", Mood::TIRED, Effect::TEARS, {1,1,.33f,0,-.05f,.3f,.13f,1}, 0},
        {"tired", Mood::TIRED, Effect::BREATH, {1,1,.33f,0,0,0,.13f,1}, 0},
        {"angry", Mood::ANGRY, Effect::ANGRY, {1,1,.25f,0,0,0,.13f,1}, 0},
        {"confused", Mood::CONFUSED, Effect::QUESTION, {1,.8f,.33f,0,0,.2f,.12f,1}, 0},
        {"winking", Mood::DEFAULT, Effect::STARS, {.08f,1,.33f,0,.04f,0,.15f,1}, 0},
        {"confident", Mood::DEFAULT, Effect::RAYS, {1,1,.33f,.2f,.045f,0,.16f,1}, 0},
    };
    for (const auto& expression : expressions) if (!strcmp(name, expression.name)) return &expression;
    return nullptr;
}

bool RoboEyes::IsExpression(const char* name) { return FindExpression(name) != nullptr; }

bool RoboEyes::ApplyExpression(const char* name) {
    auto expression = FindExpression(name);
    if (!expression) return false;
    for (int i = 0; i < 8; ++i) {
        idle_from_[i] = idle_face_[i];
        idle_to_[i] = expression->pose[i];
    }
    expression_name_ = expression->name;
    expression_gaze_ = expression->gaze;
    idle_expression_ = true;
    idle_transition_ = true;
    idle_transition_started_ = millis_();
    set_mood(expression->mood);
    StartEffect(expression->effect, 0);
    return true;
}

void RoboEyes::SetRequestedEmotion(const char* emotion) {
    requested_until_ = 0;
    emotion_hold_until_ = 0;
    acknowledgement_until_ = 0;
    SetEmotion(emotion);
    requested_until_ = millis_() + 8000;
}

void RoboEyes::SetEmotion(const char* emotion) {
    const char* e = emotion ? emotion : "neutral";
    if (requested_until_ && static_cast<int32_t>(millis_() - requested_until_) < 0 &&
        strncmp(e, "reminder_", 9) && strcmp(e, "singing")) return;
    requested_until_ = 0;
    const bool activity_reset = !strcmp(e, "neutral") || !strcmp(e, "listening");
    if (activity_reset && emotion_hold_until_ &&
        static_cast<int32_t>(millis_() - emotion_hold_until_) < 0) {
        // Keep the reply's face while still reflecting whether we are listening.
        attentive_ = !strcmp(e, "listening");
        return;
    }
    // Let the confirmation smile finish across the normal stop/standby messages.
    if (acknowledgement_until_ && static_cast<int32_t>(millis_() - acknowledgement_until_) < 0 &&
        (!strcmp(e, "neutral") || !strcmp(e, "listening"))) return;
    idle_expression_ = false;
    idle_transition_ = false;
    idle_face_[0] = idle_face_[1] = idle_face_[7] = 1.0f;
    idle_face_[2] = 0.33f;
    idle_face_[3] = idle_face_[5] = 0.0f;
    idle_face_[4] = 0.018f;
    idle_face_[6] = 0.13f;
    next_idle_ms_ = millis_() + 15000u;
    StartEffect(Effect::NONE, 0);
    acknowledgement_until_ = 0;
    emotion_hold_until_ = 0;
    dialogue_emotion_ = false;
    attentive_ = !strcmp(e, "listening") || !strcmp(e, "reminder_wait");
    thinking_ = !strcmp(e, "thinking");
    reminder_wait_ = !strcmp(e, "reminder_wait");
    if (!strcmp(e, "singing")) {
        expression_name_ = "singing";
        StartEffect(Effect::MUSIC, 0);
        set_mood(Mood::HAPPY);
        return;
    }
    if (!strcmp(e, "reminder_ack")) {
        expression_name_ = "reminder_ack";
        acknowledgement_until_ = millis_() + 2800;
        StartEffect(Effect::CONFETTI, 2800);
        set_mood(Mood::HAPPY);
        return;
    }
    if (!strcmp(e, "reminder_call")) { expression_name_ = "reminder_call"; StartEffect(Effect::RAYS, 0); set_mood(Mood::HAPPY); return; }
    if (reminder_wait_) { expression_name_ = "reminder_wait"; set_mood(Mood::DEFAULT); return; }
    if (ApplyExpression(e)) {
        dialogue_emotion_ = strcmp(e, "neutral") && strcmp(e, "relaxed") && strcmp(e, "speaking");
    } else {
        expression_name_ = thinking_ ? "thinking" : attentive_ ? "listening" : "neutral";
        set_mood(attentive_ || thinking_ ? Mood::CURIOUS : Mood::DEFAULT);
        if (thinking_) StartEffect(Effect::QUESTION, 0);
    }
}

void RoboEyes::set_mood(Mood m) {
    if (mood_ == m) return;
    ESP_LOGD(TAG, "mood %d -> %d", (int) mood_, (int) m);
    mood_ = m;
    tpx_  = 0.0f;
    tpy_  = 0.0f;
    // Keep the natural blink schedule instead of blinking at every emotion change.
}

void RoboEyes::SetSpeaking(bool speaking) {
    if (speaking) SetIdle(false);
    if (speaking && !speaking_) speaking_started_ms_ = millis_();
    if (!speaking && speaking_ && dialogue_emotion_ && !reminder_wait_) {
        emotion_hold_until_ = millis_() + 6000;
    }
    if (speaking) emotion_hold_until_ = 0;
    speaking_ = speaking;
    if (speaking && thinking_) { ApplyExpression("neutral"); }
    if (speaking) { attentive_ = false; thinking_ = false; }
}

void RoboEyes::SetIdle(bool idle) {
    if (idle_ == idle) return;
    idle_ = idle;
    if (idle) idle_started_ms_ = millis_();
    else if (!dialogue_emotion_ && !requested_until_ && effect_ != Effect::CONFETTI) StartEffect(Effect::NONE, 0);
    next_idle_ms_ = millis_() + 15000u;
    if (!idle) {
        emotion_hold_until_ = 0;
        if (!dialogue_emotion_ && !requested_until_ && idle_expression_) ApplyExpression("neutral");
    }
}

void RoboEyes::set_phase(int phase_id) {
    SetIdle(phase_id == phase_idle_);
    Mood m;
    if      (phase_id == phase_idle_)           m = Mood::DEFAULT;
    else if (phase_id == phase_listening_)       m = Mood::CURIOUS;
    else if (phase_id == phase_thinking_)        m = Mood::TIRED;
    else if (phase_id == phase_replying_)        m = Mood::HAPPY;
    else if (phase_id == phase_not_ready_)       m = Mood::TIRED;
    else if (phase_id == phase_error_)           m = Mood::ANGRY;
    else if (phase_id == phase_muted_)           m = Mood::CLOSED;
    else if (phase_id == phase_timer_finished_)  m = Mood::CONFUSED;
    else                                         m = Mood::DEFAULT;
    set_mood(m);
}

// =============================================================================
// LVGL timer callback
// =============================================================================
void RoboEyes::s_tick(lv_timer_t *t) {
    auto *self = static_cast<RoboEyes *>(lv_timer_get_user_data(t));
    if (self) self->tick();
}

// =============================================================================
// LVGL draw callback — called during LVGL render cycle
// =============================================================================
void RoboEyes::s_draw_cb(lv_event_t *e) {
    auto *self  = static_cast<RoboEyes *>(lv_event_get_user_data(e));
    lv_layer_t *layer = lv_event_get_layer(e);
    if (self && layer) self->draw_frame_(layer);
}

// =============================================================================
// tick() — update animation state, then invalidate to trigger draw callback
// =============================================================================
void RoboEyes::tick() {
    if (!draw_obj_ || !screen_) return;
    if (lv_screen_active() != screen_ || !lv_obj_is_visible(draw_obj_)) return;

    uint32_t now = millis_();
    frame_++;
    if (effect_ != Effect::NONE && effect_duration_ms_ != 0 && static_cast<uint32_t>(now - effect_started_ms_) >= effect_duration_ms_)
        effect_ = Effect::NONE;
    if (requested_until_ && static_cast<int32_t>(now - requested_until_) >= 0) {
        requested_until_ = 0;
        emotion_hold_until_ = 0;
        SetEmotion("neutral");
    }
    if (emotion_hold_until_ && static_cast<int32_t>(now - emotion_hold_until_) >= 0) {
        emotion_hold_until_ = 0;
        dialogue_emotion_ = false;
        StartEffect(Effect::NONE, 0);
        SetEmotion(attentive_ ? "listening" : "neutral");
    }
    if (acknowledgement_until_ && static_cast<int32_t>(now - acknowledgement_until_) >= 0) {
        acknowledgement_until_ = 0;
        set_mood(Mood::DEFAULT);
    }

    // Geometry morphs independently of normal blinking; each pose holds 15s.
    if (idle_ && !connecting_ && !speaking_ && !attentive_ && !thinking_ && !reminder_wait_ &&
        !emotion_hold_until_ && !requested_until_ && !acknowledgement_until_ &&
        !idle_transition_ && static_cast<int32_t>(now - next_idle_ms_) >= 0) {
        // Left/right height, roundness, smile lid, mouth curve/open/width, eye width.
        static const char* names[] = {"neutral", "happy", "curious", "surprised", "loving", "laughing",
            "peek_left", "peek_right", "daydream", "sleepy", "yawn"};
        const uint8_t count = static_cast<uint32_t>(now - idle_started_ms_) >= 120000u ? 11 : 9;
        const uint16_t all = (1u << count) - 1u;
        if ((idle_faces_used_ & all) == all) idle_faces_used_ = 0;
        uint8_t choice = rng_() % count;
        while ((idle_faces_used_ & (1u << choice)) || choice == last_idle_choice_)
            choice = (choice + 1u) % count;
        if (last_idle_choice_ == 10) choice = 0;
        idle_faces_used_ |= 1u << choice;
        last_idle_choice_ = choice;
        ApplyExpression(names[choice]);
        dialogue_emotion_ = false;
        ESP_LOGI(TAG, "idle face %u", static_cast<unsigned>(choice));
    }
    if (idle_transition_) {
        const float t = clamp_(static_cast<uint32_t>(now - idle_transition_started_) / 800.0f, 0.0f, 1.0f);
        const float eased = t * t * (3.0f - 2.0f * t);
        for (int i = 0; i < 8; ++i) idle_face_[i] = lerp_(idle_from_[i], idle_to_[i], eased);
        if (t >= 1.0f) {
            idle_transition_ = false;
            next_idle_ms_ = now + (last_idle_choice_ == 10 ? 2200u : 15000u);
        }
    }

#ifdef CONFIG_USE_ROBO_MOUTH
    // A gentle speaking-state animation, not phoneme or audio-amplitude tracking.
    const float elapsed = static_cast<uint32_t>(now - speaking_started_ms_) / 1000.0f;
    const float syllable = 0.5f + 0.5f * sinf(elapsed * 17.0f);
    const float envelope = 0.65f + 0.35f * sinf(elapsed * 5.0f);
    const float target = speaking_ && !reminder_wait_
        ? (effect_ == Effect::MUSIC ? .3f + .35f * (.5f + .5f * sinf(elapsed * 4.2f)) : syllable * envelope)
        : 0.0f;
    mouth_open_ = lerp_(mouth_open_, target, speaking_ ? 0.45f : 0.35f);
#endif

    // ── Blink state machine ───────────────────────────────────────────────────
    if (mood_ == Mood::CLOSED) {
        openness_    = lerp_(openness_, 0.0f, 0.07f);
        blink_phase_ = 0;
    } else if (mood_ == Mood::ANGRY) {
        openness_    = lerp_(openness_, 1.0f, 0.12f);
        blink_phase_ = 0;
        next_blink_ms_ = now + blink_interval_ms_;
    } else if (blink_phase_ == 0) {
        openness_ = lerp_(openness_, 1.0f, 0.14f);
        if (static_cast<int32_t>(now - next_blink_ms_) >= 0) {
            blink_phase_ = 1;
            uint32_t var = blink_variation_ms_ > 0 ? rng_() % blink_variation_ms_ : 0u;
            next_blink_ms_ = now + blink_interval_ms_ + var;
        }
    } else if (blink_phase_ == 1) {
        openness_ -= 0.22f;
        if (openness_ <= 0.01f) { openness_ = 0.0f; blink_phase_ = 2; }
    } else {
        openness_ += 0.16f;
        if (openness_ >= 0.98f) { openness_ = 1.0f; blink_phase_ = 0; }
    }

    // ── Pupil wander ──────────────────────────────────────────────────────────
    float wander_speed = 0.03f;
    switch (mood_) {
        case Mood::CURIOUS:
            wander_speed = 0.04f;
            if (static_cast<int32_t>(now - next_wander_ms_) >= 0) {
                tpx_ = (float)((int)(rng_() % 200) - 100) / 100.0f * 0.75f;
                tpy_ = (float)((int)(rng_() % 200) - 100) / 100.0f * 0.50f;
                next_wander_ms_ = now + 1800u + rng_() % 1200u;
            }
            break;
        case Mood::TIRED:
            tpx_ = lerp_(tpx_, -0.55f, 0.025f);
            tpy_ = lerp_(tpy_, -0.45f, 0.025f);
            break;
        case Mood::HAPPY:
            tpx_ = sinf((float) frame_ * 0.07f) * 0.35f;
            tpy_ = 0.0f;
            break;
        case Mood::CONFUSED:
            wander_speed = 0.12f;
            if (static_cast<int32_t>(now - next_wander_ms_) >= 0) {
                tpx_ = (float)((int)(rng_() % 200) - 100) / 100.0f * 0.60f;
                tpy_ = (float)((int)(rng_() % 200) - 100) / 100.0f * 0.30f;
                next_wander_ms_ = now + 1200u + rng_() % 800u;
            }
            break;
        default:
            if (static_cast<int32_t>(now - next_wander_ms_) >= 0) {
                tpx_ = (float)((int)(rng_() % 200) - 100) / 100.0f * 0.45f;
                tpy_ = (float)((int)(rng_() % 200) - 100) / 100.0f * 0.28f;
                next_wander_ms_ = now + wander_interval_ms_ + rng_() % 2000u;
            }
            break;
    }
    if (idle_expression_) {
        if (expression_gaze_ == -1 || expression_gaze_ == 1) {
            // Gaze stays inside fixed eye outlines, rather than moving the face.
            tpx_ = expression_gaze_ == -1 ? -1.0f : 1.0f;
            wander_speed = .07f;
            tpy_ = 0.0f;
        } else if (expression_gaze_ == 2) {
            tpx_ = .8f; tpy_ = -1.2f;
            wander_speed = .025f;
        }
        else if (effect_ == Effect::SLEEP || effect_ == Effect::BREATH) { tpx_ = 0; tpy_ = 0; }
    }
    if (attentive_) { tpx_ = 0.0f; tpy_ = 0.0f; }
    if (thinking_) { tpx_ = sinf(frame_ * 0.025f) * 0.4f; tpy_ = -0.25f; }
    px_ = lerp_(px_, tpx_, wander_speed);
    py_ = lerp_(py_, tpy_, wander_speed);
    const bool peek = idle_expression_ && (expression_gaze_ == -1 || expression_gaze_ == 1);
    peek_pupil_visibility_ = lerp_(peek_pupil_visibility_, peek ? 1.0f : 0.0f, .12f);

    // ── Bounce (HAPPY) ────────────────────────────────────────────────────────
    bounce_y_ = (mood_ == Mood::HAPPY)
                ? sinf((float) frame_ * 0.08f) * 1.5f
                : lerp_(bounce_y_, 0.0f, 0.1f);

    // ── Flicker (ANGRY / CONFUSED) ────────────────────────────────────────────
    if (mood_ == Mood::ANGRY || mood_ == Mood::CONFUSED) {
        flicker_x_ = sinf(frame_ * 0.08f) * 1.2f;
    } else {
        flicker_x_ = 0.0f;
    }

    // Request redraw; style is constant and must not be changed from draw callbacks.
    lv_obj_invalidate(draw_obj_);
}

// =============================================================================
// draw_frame_() — called from s_draw_cb during LVGL render
// =============================================================================
void RoboEyes::draw_frame_(lv_layer_t *layer) {
    // LVGL draw coordinates are absolute, including our subtitle-safe parent offset.
    lv_area_t bounds;
    lv_obj_get_coords(draw_obj_, &bounds);
    const float outline_motion = show_pupils_ ? 0.0f : 1.0f - peek_pupil_visibility_;
    const float gaze_x = px_ * 7.0f * outline_motion;
    const float gaze_y = py_ * 5.0f * outline_motion;
    const float cx = bounds.x1 + disp_w_ * 0.5f + gaze_x;
    const float cy = bounds.y1 + disp_h_ *
#ifdef CONFIG_USE_ROBO_MOUTH
        0.40f
#else
        0.5f
#endif
        + bounce_y_ + gaze_y;
    const float vis_h = (float) eye_height_ * clamp_(openness_, 0.0f, 1.0f);

    if (cyclops_) {
        draw_eye_(layer, cx + flicker_x_, cy, (float) eye_width_, vis_h, true);
    } else {
        const float total_w = (float)(eye_width_ * 2 + eye_gap_ + space_between_);
        const float lx = cx - total_w * 0.5f + (float) eye_width_ * 0.5f + flicker_x_;
        const float rx = cx + total_w * 0.5f - (float) eye_width_ * 0.5f + flicker_x_;
        draw_eye_(layer, lx, cy, (float) eye_width_, vis_h, true);
        draw_eye_(layer, rx, cy, (float) eye_width_, vis_h, false);
    }
#ifdef CONFIG_USE_ROBO_MOUTH
    const bool peeking = idle_expression_ && (expression_gaze_ != 0);
    draw_mouth_(layer, bounds.x1 + disp_w_ * 0.5f + (peeking ? 0.0f : gaze_x * 0.35f),
                bounds.y1 + disp_h_ * 0.78f + bounce_y_ * 0.3f);
#endif
    draw_effects_(layer, bounds);
}

void RoboEyes::StartEffect(Effect effect, uint32_t duration) {
    effect_ = effect;
    effect_started_ms_ = millis_();
    effect_duration_ms_ = duration;
}

void RoboEyes::draw_effects_(lv_layer_t* layer, const lv_area_t& bounds) {
    if (!connecting_ && effect_ == Effect::NONE) return;
    constexpr float pi = 3.14159265f;
    const float elapsed = static_cast<uint32_t>(millis_() - effect_started_ms_) / 1000.0f;
    const float scale = std::min(disp_w_, disp_h_) / 160.0f;
    const float cx = bounds.x1 + disp_w_ * .5f;
    const float cy = bounds.y1 + disp_h_ * .40f;
    auto dot = [&](float x, float y, float radius, uint32_t color, float opacity) {
        lv_draw_rect_dsc_t d;
        lv_draw_rect_dsc_init(&d);
        d.bg_color = lv_color_hex(color);
        d.bg_opa = static_cast<lv_opa_t>(clamp_(opacity, 0, 1) * 255);
        d.radius = LV_RADIUS_CIRCLE;
        lv_area_t area = {static_cast<int32_t>(x-radius), static_cast<int32_t>(y-radius),
                          static_cast<int32_t>(x+radius), static_cast<int32_t>(y+radius)};
        // Particles remain inside the face object, away from status and subtitles.
        area.x1 = std::max(area.x1, bounds.x1);
        area.y1 = std::max(area.y1, bounds.y1);
        area.x2 = std::min(area.x2, bounds.x2);
        area.y2 = std::min(area.y2, bounds.y2);
        if (area.x1 <= area.x2 && area.y1 <= area.y2) lv_draw_rect(layer, &d, &area);
    };
    auto line = [&](float x1, float y1, float x2, float y2, uint32_t color, float opacity) {
        const int steps = std::max(1, static_cast<int>(std::max(fabsf(x2-x1), fabsf(y2-y1)) / 2));
        for (int i=0; i<=steps; ++i) {
            const float t = static_cast<float>(i)/steps;
            dot(lerp_(x1,x2,t), lerp_(y1,y2,t), std::max(.8f, scale), color, opacity);
        }
    };
    if (connecting_) {
        const float phase = millis_() / 180.0f;
        for (int i=0; i<8; ++i) {
            const float a = i*pi/4;
            const float brightness = .2f + .8f * (.5f+.5f*cosf(a-phase));
            dot(cx+cosf(a)*9*scale, bounds.y1+15*scale+sinf(a)*9*scale,
                1.8f*scale, eye_color_, brightness);
        }
        return;
    }
    const float remaining = 1.0f - clamp_(elapsed * 1000 / std::max<uint32_t>(1,effect_duration_ms_),0,1);
    const float fade = effect_duration_ms_ == 0 ? 1.0f : std::min(1.0f, remaining * 4);
    if (effect_ == Effect::MUSIC) {
        // Draw notes geometrically: independent of the selected font's glyphs.
        for (int i = 0; i < 4; ++i) {
            const float t = fmodf(elapsed * .30f + i * .25f, 1.0f);
            const float x = cx + (i % 2 ? -1 : 1) * disp_w_ * .34f + sinf(t * 5 + i) * 3 * scale;
            const float y = cy + 18 * scale - t * disp_h_ * .36f;
            const float opacity = sinf(t * pi) * .85f;
            const uint32_t color = i % 2 ? 0xffcf70 : 0x92ddff;
            dot(x, y, 2.5f * scale, color, opacity);
            line(x + 2 * scale, y, x + 2 * scale, y - 11 * scale, color, opacity);
            line(x + 2 * scale, y - 11 * scale, x + 7 * scale, y - 8 * scale, color, opacity);
        }
    } else if (effect_ == Effect::TEARS) {
        for (int side : {-1, 1}) for (int i = 0; i < 2; ++i) {
            const float t = fmodf(elapsed * .55f + i * .5f, 1.0f);
            const float x = cx + side * disp_w_ * .30f;
            const float y = cy + eye_height_ * .45f + t * disp_h_ * .22f;
            dot(x, y, (1.5f + t) * scale, 0x80cfff, (1-t)*fade);
            line(x, y-4*scale, x, y, 0x80cfff, (1-t)*fade);
        }
    } else if (effect_ == Effect::BLUSH) {
        for (int side : {-1,1}) {
            const float x = cx+side*disp_w_*.36f;
            for (int i=-1; i<=1; ++i)
                line(x+i*5*scale,cy+eye_height_*.62f,x+(i*5+2)*scale,
                     cy+eye_height_*.62f+5*scale,0xff819a,.65f*fade);
        }
    } else if (effect_ == Effect::HEARTS) {
        for (int i=0; i<3; ++i) {
            const float t = fmodf(elapsed*.40f+i*.32f,1.0f);
            const float x = cx+(i%2 ? -1:1)*disp_w_*.35f;
            const float y = cy+8*scale-t*disp_h_*.32f;
            for (int k=0; k<36; ++k) {
                const float a=k*2*pi/36;
                const float hx=16*powf(sinf(a),3);
                const float hy=13*cosf(a)-5*cosf(2*a)-2*cosf(3*a)-cosf(4*a);
                dot(x+hx*.30f*scale,y-hy*.30f*scale,scale,0xff6c9c,(1-t)*fade);
            }
        }
    } else if (effect_ == Effect::SLEEP) {
        for (int i=0; i<3; ++i) {
            const float t=fmodf(elapsed*.23f+i*.33f,1.0f);
            const float size=(3+5*t)*scale;
            const float x=cx+disp_w_*.20f+t*disp_w_*.15f;
            const float y=cy-disp_h_*.12f-t*disp_h_*.20f;
            line(x,y,x+size,y,eye_color_,(1-t)*fade);
            line(x+size,y,x,y+size,eye_color_,(1-t)*fade);
            line(x,y+size,x+size,y+size,eye_color_,(1-t)*fade);
        }
    } else if (effect_ == Effect::STARS) {
        for (int i=0; i<4; ++i) {
            const float a=i*pi/2+pi/4;
            const float x=cx+cosf(a)*disp_w_*.43f;
            const float y=cy+sinf(a)*eye_height_*.8f;
            const float pulse=.5f+.5f*sinf(elapsed*5+i*1.7f);
            const float r=(2+3*pulse)*scale;
            line(x-r,y,x+r,y,0xffdd66,pulse*fade);
            line(x,y-r,x,y+r,0xffdd66,pulse*fade);
        }
    } else if (effect_ == Effect::DAYDREAM) {
        // Quiet ellipsis, not floating bubbles: a held, absent-minded gaze.
        for (int i=0; i<3; ++i) {
            const float opacity=.35f+.15f*sinf(elapsed*.9f+i*.4f);
            dot(cx+(17+i*6)*scale,cy-eye_height_*.6f,1.2f*scale,0xb9bdff,opacity*fade);
        }
    } else if (effect_ == Effect::QUESTION) {
        const float x=cx+disp_w_*.34f,y=cy-eye_height_*.65f+sinf(elapsed*2)*2*scale;
        // Small hand-drawn question mark avoids any dependency on font glyphs.
        float px=x-4*scale,py=y;
        for (int i=1; i<=12; ++i) {
            const float a=pi-i*pi*1.5f/12;
            const float nx=x+cosf(a)*4*scale,ny=y-sinf(a)*4*scale;
            line(px,py,nx,ny,0xffd782,.8f*fade); px=nx; py=ny;
        }
        line(px,py,x,y+6*scale,0xffd782,.8f*fade);
        dot(x,y+10*scale,1.2f*scale,0xffd782,fade);
    } else if (effect_ == Effect::RAYS) {
        const float pulse=.4f+.6f*(.5f+.5f*sinf(elapsed*3));
        for (int side : {-1,1}) for (int i=-1; i<=1; ++i) {
            const float x=cx+side*disp_w_*.36f,y=cy+i*9*scale;
            line(x,y,x+side*5*scale,y+i*3*scale,0xffdf84,pulse*fade);
        }
    } else if (effect_ == Effect::PEEK) {
        const float side=expression_gaze_ == -1 ? -1.0f : 1.0f;
        const float pulse=.5f+.5f*sinf(elapsed*2);
        const float x=cx+side*disp_w_*.42f,y=cy-eye_height_*.4f;
        line(x-side*3*scale,y-4*scale,x,y,eye_color_,(.25f+.5f*pulse)*fade);
        line(x,y,x-side*3*scale,y+4*scale,eye_color_,(.25f+.5f*pulse)*fade);
    } else if (effect_ == Effect::BUBBLES || effect_ == Effect::BREATH) {
        const bool breath=effect_ == Effect::BREATH;
        for (int i=0; i<3; ++i) {
            const float t=fmodf(elapsed*.25f+i*.33f,1.0f);
            const float x=cx+disp_w_*(.22f+t*.15f);
            const float y=cy+(breath ? disp_h_*.28f : -eye_height_*.35f)-t*disp_h_*.2f;
            const float r=(1.5f+3*t)*scale;
            for (int k=0; k<16; ++k) {
                const float a=k*2*pi/16;
                dot(x+cosf(a)*r,y+sinf(a)*r,.7f*scale,
                    breath ? 0xc3e4ff : 0xb9bdff,(1-t)*.65f*fade);
            }
        }
    } else if (effect_ == Effect::ANGRY) {
        const float x=cx+disp_w_*.22f,y=bounds.y1+9*scale;
        for (int side : {-1,1}) {
            line(x+side*3*scale,y,x+side*3*scale,y+13*scale,0xff6060,fade);
            line(x-6*scale,y+(6+side*3)*scale,x+6*scale,y+(6+side*3)*scale,0xff6060,fade);
        }
    } else if (effect_ == Effect::CONFETTI) {
        const float t=clamp_(elapsed/2.8f,0,1);
        const uint32_t colors[]={0xff8eab,0xffdb66,0x76e8cc,0x9ba7ff};
        for (int i=0; i<16; ++i) {
            const float a=pi+(i+.5f)*pi/16;
            const float x=cx+cosf(a)*disp_w_*.43f*t;
            const float y=cy+sinf(a)*disp_h_*.7f*t+disp_h_*.7f*t*t;
            dot(x,y,1.5f*scale,colors[i%4],fade);
        }
    }
}

void RoboEyes::draw_mouth_(lv_layer_t* layer, float cx, float cy) {
    const bool ambient = idle_expression_;
    const float width = disp_w_ * (ambient ? idle_face_[6] : (mood_ == Mood::HAPPY ? 0.18f : 0.13f));
    const float stroke = std::max(2.0f, disp_w_ / 90.0f);
    lv_draw_rect_dsc_t d;
    lv_draw_rect_dsc_init(&d);
    d.bg_color = lv_color_hex(mouth_color_);
    d.bg_opa = LV_OPA_COVER;
    d.radius = LV_RADIUS_CIRCLE;
    d.border_width = 0;

    const bool talking = speaking_ && !reminder_wait_;
    if (ambient && !talking) {
        // A continuous contour blends the smile into an open oval.
        const float curve = disp_h_ * idle_face_[4];
        const float opening = disp_h_ * .065f * idle_face_[5];
        const int steps = std::max(16, static_cast<int>(width));
        for (int i = 0; i <= steps; ++i) {
            const float t = 2.0f * i / steps - 1.0f;
            const float arc = sqrtf(std::max(0.0f, 1.0f - t * t));
            const float x = cx + t * width * .5f;
            const float y = cy + curve * (1.0f - t * t);
            lv_area_t column = {static_cast<int32_t>(x - stroke / 2),
                static_cast<int32_t>(y - opening * arc - stroke / 2),
                static_cast<int32_t>(x + stroke / 2),
                static_cast<int32_t>(y + opening * arc + stroke / 2)};
            lv_draw_rect(layer, &d, &column);
        }
        return;
    }
    if (talking || mouth_open_ > 0.08f || mood_ == Mood::CURIOUS || mood_ == Mood::CONFUSED) {
        // Small rounded opening; questioning expressions retain a subtle "o" at rest.
        const float opening = std::max(mouth_open_, talking ? 0.0f : 0.18f);
        const float w = width * (0.45f + opening * 0.45f);
        const float h = stroke + disp_h_ * 0.10f * opening;
        lv_area_t area = {static_cast<int32_t>(cx - w / 2), static_cast<int32_t>(cy - h / 2),
                          static_cast<int32_t>(cx + w / 2), static_cast<int32_t>(cy + h / 2)};
        lv_draw_rect(layer, &d, &area);
        return;
    }

    // Overlapping round dots form a smooth smile/frown without extra LVGL objects.
    float curve = disp_h_ * 0.018f;
    if (mood_ == Mood::HAPPY) curve = disp_h_ * 0.055f;
    else if (mood_ == Mood::TIRED) curve = -disp_h_ * 0.035f;
    else if (mood_ == Mood::ANGRY || mood_ == Mood::CLOSED) curve = 0.0f;
    const int steps = std::max(12, static_cast<int>(width));
    for (int i = 0; i <= steps; ++i) {
        const float t = 2.0f * i / steps - 1.0f;
        const float x = cx + t * width * 0.5f;
        const float y = cy + curve * (1.0f - t * t);
        lv_area_t dot = {static_cast<int32_t>(x - stroke / 2), static_cast<int32_t>(y - stroke / 2),
                         static_cast<int32_t>(x + stroke / 2), static_cast<int32_t>(y + stroke / 2)};
        lv_draw_rect(layer, &d, &dot);
    }
}

// =============================================================================
// draw_eye_() — one eye body + mood overlay + optional pupil
// =============================================================================
void RoboEyes::draw_eye_(lv_layer_t *layer, float cx, float cy,
                          float ew, float vis_h, bool is_left) {
    const bool ambient = idle_expression_;
    if (ambient) {
        vis_h *= idle_face_[is_left ? 0 : 1];
        ew *= idle_face_[7];
    }
    vis_h = std::max(2.0f, vis_h); // Closed eyes remain visible as two thin lines.

    const float x = cx - ew * 0.5f;
    const float y = cy - vis_h * 0.5f;
    const float r_raw = ambient ? ew * idle_face_[2] * (vis_h / eye_height_)
                                : (float) border_radius_ * (vis_h / (float) eye_height_);
    const float r = clamp_(r_raw, 0.0f, std::min(ew * 0.5f, vis_h * 0.5f));

    const lv_color_t eye_col = lv_color_hex(eye_color_);
    const lv_color_t bg_col  = lv_color_hex(bg_color_);

    // ── 1. Eye body ───────────────────────────────────────────────────────────
    {
        lv_draw_rect_dsc_t d;
        lv_draw_rect_dsc_init(&d);
        d.bg_color     = eye_col;
        d.bg_opa       = LV_OPA_COVER;
        d.radius       = (lv_coord_t) r;
        d.border_width = 0;
        lv_area_t a = {
            (lv_coord_t) x,
            (lv_coord_t) y,
            (lv_coord_t)(x + ew    - 1.0f),
            (lv_coord_t)(y + vis_h - 1.0f),
        };
        lv_draw_rect(layer, &d, &a);
    }

    if (ambient && idle_face_[3] > 0.001f) {
        lv_draw_rect_dsc_t cover;
        lv_draw_rect_dsc_init(&cover);
        cover.bg_color = bg_col;
        cover.bg_opa = LV_OPA_COVER;
        lv_area_t area = {static_cast<int32_t>(x),
            static_cast<int32_t>(y + vis_h * (1.0f - idle_face_[3])),
            static_cast<int32_t>(x + ew), static_cast<int32_t>(y + vis_h + 1)};
        lv_draw_rect(layer, &cover, &area);
    }

    // ── 2. Mood eyelid overlay ─────────────────────────────────────────────────
    if (openness_ > 0.32f) {
        switch (mood_) {

            case Mood::ANGRY: {
                // Inner top corner cut → angry brow
                // NOTE: lv_draw_triangle_dsc_t in LVGL 9 uses .color and .opa
                lv_draw_triangle_dsc_t td;
                lv_draw_triangle_dsc_init(&td);
                td.color = bg_col;
                td.opa   = LV_OPA_COVER;
                if (is_left) {
                    td.p[0] = {(lv_value_precise_t) cx,      (lv_value_precise_t) y};
                    td.p[1] = {(lv_value_precise_t)(x + ew), (lv_value_precise_t) y};
                    td.p[2] = {(lv_value_precise_t)(x + ew), (lv_value_precise_t)(y + vis_h * 0.44f)};
                } else {
                    td.p[0] = {(lv_value_precise_t) x,       (lv_value_precise_t) y};
                    td.p[1] = {(lv_value_precise_t) cx,      (lv_value_precise_t) y};
                    td.p[2] = {(lv_value_precise_t) x,       (lv_value_precise_t)(y + vis_h * 0.44f)};
                }
                lv_draw_triangle(layer, &td);
                break;
            }

            case Mood::TIRED: {
                // Outer top corner cut → droopy brow
                lv_draw_triangle_dsc_t td;
                lv_draw_triangle_dsc_init(&td);
                td.color = bg_col;
                td.opa   = LV_OPA_COVER;
                if (is_left) {
                    td.p[0] = {(lv_value_precise_t) x,       (lv_value_precise_t) y};
                    td.p[1] = {(lv_value_precise_t) cx,      (lv_value_precise_t) y};
                    td.p[2] = {(lv_value_precise_t) x,       (lv_value_precise_t)(y + vis_h * 0.44f)};
                } else {
                    td.p[0] = {(lv_value_precise_t) cx,      (lv_value_precise_t) y};
                    td.p[1] = {(lv_value_precise_t)(x + ew), (lv_value_precise_t) y};
                    td.p[2] = {(lv_value_precise_t)(x + ew), (lv_value_precise_t)(y + vis_h * 0.44f)};
                }
                lv_draw_triangle(layer, &td);
                break;
            }

            case Mood::HAPPY: {
                // Bg rect covers bottom 40% → smile arch
                lv_draw_rect_dsc_t rd;
                lv_draw_rect_dsc_init(&rd);
                rd.bg_color     = bg_col;
                rd.bg_opa       = LV_OPA_COVER;
                rd.radius       = 0;
                rd.border_width = 0;
                lv_area_t ra = {
                    (lv_coord_t) x,
                    (lv_coord_t)(y + vis_h * 0.60f),
                    (lv_coord_t)(x + ew - 1.0f),
                    (lv_coord_t)(y + vis_h + 6.0f),
                };
                lv_draw_rect(layer, &rd, &ra);
                break;
            }

            default:
                break;
        }
    }

    // ── 3. Pupil ───────────────────────────────────────────────────────────────
    if ((show_pupils_ || peek_pupil_visibility_ > .01f) && pupil_size_ > 0 && openness_ > 0.14f) {
        const float pr          = (float) pupil_size_ * (vis_h / (float) eye_height_);
        // Small oval pupils like the reference; both eyes look in the same direction.
        const float half_w = show_pupils_ ? pr : pr * .48f;
        const float half_h = show_pupils_ ? pr : pr * .70f;
        const float max_px_off  = std::max(0.0f, ew * 0.5f - half_w - 1.0f);
        const float max_py_off  = std::max(0.0f, vis_h * 0.5f - half_h - 1.0f);
        const float ox  = clamp_(px_ * (ew    * 0.22f), -max_px_off, max_px_off);
        const float oy  = clamp_(py_ * (vis_h * 0.22f), -max_py_off, max_py_off);
        const float pu_x = cx + ox;
        const float pu_y = clamp_(cy + oy, y + half_h, y + vis_h - half_h);

        lv_draw_rect_dsc_t pd;
        lv_draw_rect_dsc_init(&pd);
        pd.bg_color     = bg_col;
        pd.bg_opa       = static_cast<lv_opa_t>(show_pupils_ ? 255.0f : 255.0f * peek_pupil_visibility_);
        pd.radius       = LV_RADIUS_CIRCLE;
        pd.border_width = 0;
        lv_area_t pa = {
            (lv_coord_t)(pu_x - half_w),
            (lv_coord_t)(pu_y - half_h),
            (lv_coord_t)(pu_x + half_w),
            (lv_coord_t)(pu_y + half_h),
        };
        lv_draw_rect(layer, &pd, &pa);
    }
}

}  // namespace robo_eyes

