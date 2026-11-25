#pragma once

#include <cstdint>

#if defined(__x86_64__)
#include <x86intrin.h>

#include <chrono>
#endif

namespace perf {

[[nodiscard]] inline std::uint64_t ReadCounter() {
#if defined(__aarch64__)
    std::uint64_t ticks;
    // Retrieve the value of cntvct_el0 register which increments monotonically at a fixed rate
    asm volatile("mrs %0, cntvct_el0" : "=r"(ticks));
    return ticks;
#elif defined(__x86_64__)
    return __rdtsc();
#else
#error "perf::ReadCounter needs a cycle counter for this architecture"
#endif
}

[[nodiscard]] inline std::uint64_t CounterFrequency() {
#if defined(__aarch64__)
    std::uint64_t freq;
    // Retrieve the value of cntfrq_el0 register which gives the number of ticks cntvct_el0
    // increments by per second
    asm volatile("mrs %0, cntfrq_el0" : "=r"(freq));
    return freq;
#else
    using Clock = std::chrono::steady_clock;
    const auto start_time = Clock::now();
    const std::uint64_t start_ticks = ReadCounter();
    while (Clock::now() - start_time < std::chrono::milliseconds(20)) {
    }
    const std::uint64_t ticks = ReadCounter() - start_ticks;
    const auto nanos =
        std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now() - start_time).count();
    return static_cast<std::uint64_t>(static_cast<unsigned __int128>(ticks) * 1'000'000'000ULL /
                                      static_cast<std::uint64_t>(nanos));
#endif
}

inline const std::uint64_t kCounterFrequency = CounterFrequency();

[[nodiscard]] inline std::uint64_t TicksToNanos(std::uint64_t ticks) {
    return static_cast<std::uint64_t>(static_cast<unsigned __int128>(ticks) * 1'000'000'000ULL /
                                      kCounterFrequency);
}

}  // namespace perf
