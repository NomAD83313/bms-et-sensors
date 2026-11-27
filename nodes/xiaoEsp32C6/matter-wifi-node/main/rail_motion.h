#pragma once

#include <algorithm>
#include <cmath>
#include <cstdint>

enum class RailState : uint8_t { Unknown, Stop, Left, Right };

class RailMotion {
public:
    void sample(float raw_mg, int64_t time_ms)
    {
        const int64_t delta_ms = time_ms - last_ms_;
        last_ms_ = time_ms;
        if (delta_ms <= 0 || delta_ms > 100) {
            *this = RailMotion{};
            last_ms_ = time_ms;
        }
        if (state == RailState::Unknown) {
            if (!calibration_samples_ || std::abs(raw_mg - bias_) > 20) {
                bias_ = raw_mg;
                calibration_samples_ = 0;
            }
            bias_ += (raw_mg - bias_) / ++calibration_samples_;
            if (calibration_samples_ >= 100) {
                state = RailState::Stop;
            }
            return;
        }

        filtered_ += (raw_mg - bias_ - filtered_) * 0.4f;
        const float acceleration = std::abs(filtered_) >= 12 ? filtered_ : 0;
        const float dt = delta_ms * 0.001f;
        if (state == RailState::Stop && acceleration == 0) {
            bias_ += (raw_mg - bias_) * 0.005f;
            velocity_ *= 0.8f;
        } else {
            velocity_ += acceleration * dt;
        }

        const int sign = state == RailState::Right ? 1 : state == RailState::Left ? -1 : 0;
        RailState next = state;
        if (velocity_ >= 5 && sign <= 0) {
            next = RailState::Right;
        } else if (velocity_ <= -5 && sign >= 0) {
            next = RailState::Left;
        }
        if (sign != 0) {
            peak_ = std::max(peak_, std::abs(velocity_));
            braking_ |= acceleration * sign < 0;
            if (braking_ && acceleration == 0 &&
                std::abs(velocity_) <= std::max(2.0f, peak_ * 0.25f)) {
                next = RailState::Stop;
            }
        }

        if (next != candidate_) {
            candidate_ = next;
            candidate_ms_ = time_ms;
        }
        const int dwell_ms = next == RailState::Stop ? 120 : 30;
        if (next != state && time_ms - candidate_ms_ >= dwell_ms) {
            state = next;
            transition_ms = candidate_ms_;
            peak_ = std::abs(velocity_);
            braking_ = false;
            if (state == RailState::Stop) {
                velocity_ = 0;
            }
        }
    }

    RailState state = RailState::Unknown;
    int64_t transition_ms = 0;

private:
    float bias_ = 0;
    float filtered_ = 0;
    float velocity_ = 0;
    float peak_ = 0;
    unsigned calibration_samples_ = 0;
    bool braking_ = false;
    int64_t last_ms_ = 0;
    int64_t candidate_ms_ = 0;
    RailState candidate_ = RailState::Unknown;
};
